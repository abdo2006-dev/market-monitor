"""Deterministic acquisition adapter for the isolated Vercel user-test preview.

This module never reads a storefront. It is reachable only through the three
gates in ``domain.acquisition._preview_fixture_enabled`` and is therefore inert
in production and ordinary development/test runs.
"""

from __future__ import annotations

from typing import Any


def acquire_fixture_catalog(competitor: dict) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selector = competitor.get("selector_config") or {}
    state = selector.get("preview_export_state", "complete")
    base_url = str(competitor.get("base_url") or "https://preview.invalid").rstrip("/")
    price = float(selector.get("preview_fixture_price", 84.99))
    stock_status = str(selector.get("preview_fixture_stock_status", "in_stock"))

    observations = [
        {
            "title": "Batwing",
            "price": price,
            "currency": "USD",
            "url": f"{base_url}/products/batwing",
            "image_url": None,
            "stock_status": stock_status,
            "sku": "PREVIEW-BATWING",
            "external_id": "preview-batwing",
            "category": "MM2 Knives",
        },
        {
            "title": "Elderwood Scythe",
            "price": round(price + 18.5, 2),
            "currency": "USD",
            "url": f"{base_url}/products/elderwood-scythe",
            "image_url": None,
            "stock_status": "in_stock",
            "sku": "PREVIEW-ELDERWOOD",
            "external_id": "preview-elderwood",
            "category": "MM2 Knives",
        },
        {
            "title": "Harvester",
            "price": round(price + 31.0, 2),
            "currency": "USD",
            "url": f"{base_url}/products/harvester",
            "image_url": None,
            "stock_status": "out_of_stock",
            "sku": "PREVIEW-HARVESTER",
            "external_id": "preview-harvester",
            "category": "MM2 Knives",
        },
    ]
    telemetry: dict[str, Any] = {
        "strategy": f"preview_fixture_{state}",
        "pages_fetched": 2,
        "request_count": 2,
    }

    if state == "partial":
        telemetry["page_cap_reached"] = True
        return observations[:2], telemetry
    if state == "suspicious_empty":
        telemetry.update({"pages_fetched": 1, "request_count": 1})
        return [], telemetry
    if state == "failed":
        telemetry.update({
            "pages_fetched": 0,
            "request_count": 1,
            "failure_category": "acquisition_error",
            "failure_message": "Preview fixture acquisition failed",
            "failure_retryable": False,
        })
        return [], telemetry
    return observations, telemetry
