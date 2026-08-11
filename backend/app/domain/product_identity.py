"""Canonical identity rules for one logical product at one competitor.

The persisted raw URL and external ID remain source evidence.  These helpers
derive the two database keys used for matching and integrity enforcement.
"""
from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class ProductIdentityError(ValueError):
    """An observation cannot be assigned a safe logical-product identity."""


_TRACKING_QUERY_NAMES = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def canonicalize_product_url(url: str, scrape_type: str | None = None) -> str:
    """Return the conservative canonical URL used within one competitor.

    Fragments and known tracking parameters are non-identity data. Query
    parameters not known to be tracking are retained and sorted because some
    generic storefronts identify products in the query string.
    """
    value = (url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ProductIdentityError("product URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ProductIdentityError("product URL must not contain credentials")

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower().removeprefix("www.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProductIdentityError("product URL contains an invalid port") from exc
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    if scrape_type == "shopify_json" and path.startswith("/product/"):
        # The current Storefront GraphQL extractor emits /product/<handle>,
        # while products.json emits Shopify's canonical /products/<handle>.
        path = "/products/" + path[len("/product/") :]

    query_items = []
    for name, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = name.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_NAMES:
            continue
        query_items.append((name, query_value))
    query_items.sort()

    return urlunsplit((scheme, host, path, urlencode(query_items, doseq=True), ""))


def product_identity_key(external_id: str | None) -> str | None:
    """Derive a product-level key from a source external ID.

    Shopify currently emits ``product_id:variant_id`` even though Market
    Monitor stores one row per product and merely samples one variant.  The
    product component is stable; the selected variant is not. Salla emits a
    product-level ID already. Generic Playwright emits no external ID.
    """
    value = str(external_id or "").strip()
    if not value or value.lower() in {"none", "null"}:
        return None

    shopify_pair = re.fullmatch(r"(\d+):(\d+)", value)
    if shopify_pair:
        value = shopify_pair.group(1)
    if value.lower() in {"none", "null"} or value.startswith("None:"):
        return None
    return f"external-product:{value}"


def prepare_product_observations(
    observations: Iterable[dict], scrape_type: str | None
) -> tuple[list[dict], list[dict]]:
    """Canonicalize and deterministically collapse duplicate observations.

    Connected observations sharing either canonical URL or identity key form
    one logical product. The richest observation wins; a stable JSON tie-break
    makes the result independent of payload order. Conflict metadata is
    returned for structured logging.
    """
    prepared: list[dict] = []
    for item in observations:
        raw = dict(item)
        raw["canonical_url"] = canonicalize_product_url(raw.get("url", ""), scrape_type)
        raw["identity_key"] = product_identity_key(raw.get("external_id"))
        prepared.append(raw)

    parent = list(range(len(prepared)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    seen_urls: dict[str, int] = {}
    seen_keys: dict[str, int] = {}
    for index, item in enumerate(prepared):
        canonical_url = item["canonical_url"]
        if canonical_url in seen_urls:
            union(index, seen_urls[canonical_url])
        else:
            seen_urls[canonical_url] = index
        identity_key = item.get("identity_key")
        if identity_key:
            if identity_key in seen_keys:
                union(index, seen_keys[identity_key])
            else:
                seen_keys[identity_key] = index

    groups: dict[int, list[dict]] = {}
    for index, item in enumerate(prepared):
        groups.setdefault(find(index), []).append(item)

    def rank(item: dict) -> tuple[int, str]:
        completeness = sum(
            value not in (None, "", "unknown")
            for value in (
                item.get("external_id"),
                item.get("price"),
                item.get("stock_status"),
                item.get("sku"),
                item.get("image_url"),
                item.get("category"),
                item.get("title"),
            )
        )
        stable = json.dumps(item, sort_keys=True, default=str, separators=(",", ":"))
        return completeness, stable

    winners: list[dict] = []
    conflicts: list[dict] = []
    for group in groups.values():
        winner = max(group, key=rank)
        winners.append(winner)
        if len(group) > 1:
            conflicts.append(
                {
                    "canonical_urls": sorted({item["canonical_url"] for item in group}),
                    "identity_keys": sorted(
                        {item["identity_key"] for item in group if item.get("identity_key")}
                    ),
                    "observations": len(group),
                    "selected_canonical_url": winner["canonical_url"],
                }
            )

    winners.sort(key=lambda item: (item["canonical_url"], item.get("identity_key") or ""))
    return winners, conflicts
