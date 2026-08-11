#!/usr/bin/env python
"""Explicitly consolidate duplicate products while preserving dependent history.

Dry-run is the default. Apply requires a backup acknowledgement, an exact
confirmation phrase, and a JSON plan path outside the repository.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402
from scripts.product_integrity_common import (  # noqa: E402
    build_audit,
    load_product_rows,
    logical_duplicate_groups,
    select_canonical_row,
    select_state_donor,
)


CONFIRMATION = "MERGE_DUPLICATE_PRODUCTS"


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


async def consolidate(connection, groups: list[list[dict]]) -> dict:
    merged_rows = 0
    snapshots_repointed = 0
    events_repointed = 0
    for group in groups:
        canonical = select_canonical_row(group)
        donor = select_state_donor(group)
        source_ids = sorted(row["id"] for row in group if row["id"] != canonical["id"])
        identity_sources = [row for row in group if row.get("identity_key")]
        identity_source = select_state_donor(identity_sources) if identity_sources else donor

        snapshot_result = await connection.execute(
            text(
                "UPDATE product_snapshots SET product_id = :canonical_id "
                "WHERE product_id = ANY(:source_ids)"
            ),
            {"canonical_id": canonical["id"], "source_ids": source_ids},
        )
        event_result = await connection.execute(
            text(
                "UPDATE events SET product_id = :canonical_id "
                "WHERE product_id = ANY(:source_ids)"
            ),
            {"canonical_id": canonical["id"], "source_ids": source_ids},
        )
        await connection.execute(
            text(
                """
                UPDATE products SET
                    external_id = :external_id,
                    title = :title,
                    normalized_title = :normalized_title,
                    category = :category,
                    url = :url,
                    image_url = :image_url,
                    current_price = :current_price,
                    currency = :currency,
                    stock_status = :stock_status,
                    sku = :sku,
                    first_seen_at = :first_seen_at,
                    last_seen_at = :last_seen_at,
                    last_checked_at = :last_checked_at,
                    active = :active,
                    consecutive_misses = :consecutive_misses
                WHERE id = :canonical_id
                """
            ),
            {
                "external_id": identity_source["external_id"],
                "title": donor["title"],
                "normalized_title": donor["normalized_title"],
                "category": donor["category"],
                "url": donor["url"],
                "image_url": donor["image_url"],
                "current_price": donor["current_price"],
                "currency": donor["currency"],
                "stock_status": donor["stock_status"],
                "sku": donor["sku"],
                "first_seen_at": min(row["first_seen_at"] for row in group),
                "last_seen_at": max(row["last_seen_at"] for row in group),
                "last_checked_at": max(row["last_checked_at"] for row in group),
                "active": donor["active"],
                "consecutive_misses": donor["consecutive_misses"],
                "canonical_id": canonical["id"],
            },
        )
        delete_result = await connection.execute(
            text("DELETE FROM products WHERE id = ANY(:source_ids)"),
            {"source_ids": source_ids},
        )
        snapshots_repointed += snapshot_result.rowcount
        events_repointed += event_result.rowcount
        merged_rows += delete_result.rowcount

    return {
        "duplicate_rows_consolidated": merged_rows,
        "snapshots_repointed": snapshots_repointed,
        "events_repointed": events_repointed,
    }


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform consolidation")
    parser.add_argument("--backup-confirmed", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--plan-json", type=Path)
    args = parser.parse_args()

    try:
        async with engine.connect() as connection:
            if args.apply:
                if not args.backup_confirmed or args.confirm != CONFIRMATION or not args.plan_json:
                    parser.error(
                        "--apply requires --backup-confirmed, "
                        f"--confirm {CONFIRMATION}, and --plan-json <path>"
                    )
                await connection.execute(
                    text(
                        "LOCK TABLE products, product_snapshots, events "
                        "IN SHARE ROW EXCLUSIVE MODE"
                    )
                )
            else:
                await connection.execute(text("SET TRANSACTION READ ONLY"))

            rows, invalid = await load_product_rows(connection)
            audit = build_audit(rows, invalid)
            print(json.dumps(audit["summary"], indent=2))
            if invalid:
                await connection.rollback()
                print("Refusing consolidation: invalid product identity rows exist.", file=sys.stderr)
                return 2

            groups = logical_duplicate_groups(rows)
            if not args.apply:
                print(json.dumps(audit["logical_duplicate_groups"], indent=2, default=_json_default))
                await connection.rollback()
                return 0

            plan_path = args.plan_json.expanduser().resolve()
            repository = Path(__file__).resolve().parents[2]
            if repository == plan_path or repository in plan_path.parents:
                parser.error("--plan-json must be outside the repository")
            try:
                with plan_path.open("x", encoding="utf-8") as plan_file:
                    plan_file.write(
                        json.dumps(audit, indent=2, default=_json_default) + "\n"
                    )
            except FileExistsError:
                parser.error("--plan-json already exists; refusing to overwrite it")

            result = await consolidate(connection, groups)
            remaining_rows, remaining_invalid = await load_product_rows(connection)
            remaining = logical_duplicate_groups(remaining_rows)
            if remaining or remaining_invalid:
                raise RuntimeError("post-consolidation identity audit still reports conflicts")
            await connection.commit()
            print(json.dumps(result, indent=2))
            print(f"Recovery plan written to {plan_path}")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
