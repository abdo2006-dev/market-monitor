"""Shared read-only analysis for product duplicate audit/remediation scripts."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from sqlalchemy import text

from app.domain.product_identity import canonicalize_product_url, product_identity_key


PRODUCT_ROWS_SQL = text(
    """
    SELECT
        p.id,
        p.competitor_id,
        c.name AS competitor_name,
        c.scrape_type,
        p.url,
        p.external_id,
        p.title,
        p.current_price,
        p.currency,
        p.stock_status,
        p.active,
        p.consecutive_misses,
        p.first_seen_at,
        p.last_seen_at,
        p.last_checked_at,
        p.normalized_title,
        p.category,
        p.image_url,
        p.sku,
        (SELECT count(*) FROM product_snapshots s WHERE s.product_id = p.id) AS snapshot_count,
        (SELECT count(*) FROM events e WHERE e.product_id = p.id) AS event_count
    FROM products p
    JOIN competitors c ON c.id = p.competitor_id
    ORDER BY p.competitor_id, p.id
    """
)


async def load_product_rows(connection) -> tuple[list[dict], list[dict]]:
    rows = [dict(row) for row in (await connection.execute(PRODUCT_ROWS_SQL)).mappings()]
    invalid: list[dict] = []
    for row in rows:
        try:
            row["canonical_url"] = canonicalize_product_url(
                row["url"], row["scrape_type"]
            )
        except ValueError as exc:
            invalid.append(
                {
                    "competitor_id": row["competitor_id"],
                    "product_id": row["id"],
                    "reason": str(exc),
                }
            )
            row["canonical_url"] = None
        row["identity_key"] = product_identity_key(row["external_id"])
    return rows, invalid


def grouped_duplicates(rows: Iterable[dict], key_name: str) -> list[dict]:
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in rows:
        value = row.get(key_name)
        if value:
            grouped[(row["competitor_id"], value)].append(row)
    return [
        describe_group(group, {key_name: value})
        for (competitor_id, value), group in sorted(grouped.items())
        if len(group) > 1
    ]


def logical_duplicate_groups(rows: list[dict]) -> list[list[dict]]:
    """Return transitive groups linked by canonical URL or identity key."""
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    seen: dict[tuple[int, str, str], int] = {}
    for index, row in enumerate(rows):
        for kind in ("canonical_url", "identity_key"):
            value = row.get(kind)
            if not value:
                continue
            key = (row["competitor_id"], kind, value)
            if key in seen:
                union(index, seen[key])
            else:
                seen[key] = index

    groups: dict[int, list[dict]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[find(index)].append(row)
    return [group for group in groups.values() if len(group) > 1]


def select_canonical_row(group: list[dict]) -> dict:
    """Choose the durable row ID; current state is selected separately."""
    far_future = datetime.max.replace(tzinfo=group[0]["first_seen_at"].tzinfo)
    return min(
        group,
        key=lambda row: (
            0 if row.get("identity_key") else 1,
            row.get("first_seen_at") or far_future,
            row["id"],
        ),
    )


def select_state_donor(group: list[dict]) -> dict:
    """Choose the most recently checked row as current commercial state."""
    far_past = datetime.min.replace(tzinfo=group[0]["last_checked_at"].tzinfo)
    return max(
        group,
        key=lambda row: (
            row.get("last_checked_at") or far_past,
            row.get("last_seen_at") or far_past,
            bool(row.get("active")),
            row["id"],
        ),
    )


def describe_group(group: list[dict], identity: dict | None = None) -> dict:
    canonical = select_canonical_row(group)
    donor = select_state_donor(group)
    return {
        "competitor_id": canonical["competitor_id"],
        "competitor_name": canonical["competitor_name"],
        "scrape_type": canonical["scrape_type"],
        "identity": identity
        or {
            "canonical_urls": sorted(
                {row["canonical_url"] for row in group if row.get("canonical_url")}
            ),
            "identity_keys": sorted(
                {row["identity_key"] for row in group if row.get("identity_key")}
            ),
        },
        "canonical_product_id": canonical["id"],
        "state_donor_product_id": donor["id"],
        "affected_product_ids": sorted(row["id"] for row in group),
        "snapshot_count": sum(row["snapshot_count"] for row in group),
        "event_count": sum(row["event_count"] for row in group),
        "first_seen_at": min(row["first_seen_at"] for row in group),
        "last_seen_at": max(row["last_seen_at"] for row in group),
        "last_checked_at": max(row["last_checked_at"] for row in group),
        "rows": [
            {
                "id": row["id"],
                "url": row["url"],
                "canonical_url": row["canonical_url"],
                "external_id": row["external_id"],
                "identity_key": row["identity_key"],
                "snapshot_count": row["snapshot_count"],
                "event_count": row["event_count"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "last_checked_at": row["last_checked_at"],
                "current_price": row["current_price"],
                "currency": row["currency"],
                "stock_status": row["stock_status"],
                "active": row["active"],
                "consecutive_misses": row["consecutive_misses"],
            }
            for row in sorted(group, key=lambda item: item["id"])
        ],
    }


def build_audit(rows: list[dict], invalid: list[dict]) -> dict:
    logical = logical_duplicate_groups(rows)
    exact_url = grouped_duplicates(rows, "url")
    external_id = grouped_duplicates(rows, "external_id")
    canonical_url = grouped_duplicates(rows, "canonical_url")
    identity_key = grouped_duplicates(rows, "identity_key")
    return {
        "read_only": True,
        "products_inspected": len(rows),
        "invalid_identity_rows": invalid,
        "summary": {
            "exact_url_groups": len(exact_url),
            "external_id_groups": len(external_id),
            "canonical_url_groups": len(canonical_url),
            "product_identity_key_groups": len(identity_key),
            "logical_duplicate_groups": len(logical),
            "affected_products": sum(len(group) for group in logical),
        },
        "exact_url_groups": exact_url,
        "external_id_groups": external_id,
        "canonical_url_groups": canonical_url,
        "product_identity_key_groups": identity_key,
        "logical_duplicate_groups": [describe_group(group) for group in logical],
    }
