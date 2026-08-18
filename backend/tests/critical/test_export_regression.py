"""Phase 1D collection-export provenance, compatibility, and safety coverage."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.acquisition import AcquisitionFailure, AcquisitionResult
from app.models import ScrapeRun
from tests.conftest import requires_db
from tests.critical.factories import make_competitor, make_product, observation


pytestmark = [pytest.mark.critical, requires_db]

COLLECTION_URL = "https://alpha.example/collections/murder-mystery-2"
OBSERVED_AT = datetime(2026, 8, 12, 7, 30, tzinfo=timezone.utc)


def shopify_fixture() -> list[dict]:
    return [
        observation("Corrupted Batwing", price=25.50, currency="USD", url="https://alpha.example/products/corrupted-batwing", image_url="https://cdn.shopify.com/x/corrupted-batwing.png", stock_status="in_stock", sku="MM2-CB-01", external_id="111:222", category="Murder Mystery 2"),
        observation("Chill Knife", price=4.00, currency="USD", url="https://alpha.example/products/chill-knife", image_url="https://cdn.shopify.com/x/chill-knife.png", stock_status="out_of_stock", sku="MM2-CK-01", external_id="333:444", category="Murder Mystery 2"),
        observation("Priceless Relic", price=None, currency="USD", url="https://alpha.example/products/priceless-relic", stock_status="unknown", category="Murder Mystery 2"),
    ]


def acquisition(
    products: list[dict], *, completeness: str = "complete", pages: int = 1,
    page_cap_reached: bool = False, reason: str | None = None,
) -> AcquisitionResult:
    observations = [{**product, "observed_at": OBSERVED_AT} for product in products]
    return AcquisitionResult(
        observations=observations,
        strategy="shopify_products_json_aiohttp",
        started_at=OBSERVED_AT - timedelta(seconds=2),
        completed_at=OBSERVED_AT + timedelta(seconds=1),
        pages_fetched=pages,
        request_count=pages,
        completeness=completeness,  # type: ignore[arg-type]
        page_cap_reached=page_cap_reached,
        completeness_reason=reason or {"complete": "Adapter reached a catalog end signal", "partial": "Pagination cap reached while another page may exist", "suspicious_empty": "Unexpected zero-product result; absence inference disabled"}[completeness],
    )


def patch_export_acquisition(monkeypatch, result):
    captured = {}

    async def fake_acquire(competitor, **kwargs):
        captured["competitor"] = competitor
        captured["kwargs"] = kwargs
        if isinstance(result, Exception):
            raise result
        return result

    import app.api.exports as exports_module
    monkeypatch.setattr(exports_module, "acquire_collection", fake_acquire)
    return captured


async def alpha_competitor(session):
    competitor = await make_competitor(session, "Alpha Store", "https://alpha.example", scrape_type="shopify_json")
    await session.commit()
    return competitor


async def export(api_client, competitor_id, **params):
    return await api_client.get("/api/exports/collection-prices", params={"competitor_id": competitor_id, "collection_url": COLLECTION_URL, **params})


def provenance(response) -> dict[str, str]:
    return {key.lower(): value for key, value in response.headers.items() if key.lower().startswith("x-market-monitor-export-")}


async def make_complete_run(session, competitor, *, observed_at=OBSERVED_AT):
    run = ScrapeRun(
        competitor_id=competitor.id, status="success", trigger="manual",
        completeness="complete", observation_completed_at=observed_at,
        terminal_at=observed_at + timedelta(seconds=1), finished_at=observed_at + timedelta(seconds=1),
    )
    session.add(run)
    await session.flush()
    return run


# Validation and compatibility -------------------------------------------------

async def test_unknown_competitor_returns_404(db_session, api_client):
    resp = await export(api_client, 999999)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Competitor not found"


@pytest.mark.parametrize("bad_url", ["not-a-url", "ftp://alpha.example/collections/x", "/collections/relative-only", "javascript:alert(1)"])
async def test_non_absolute_or_non_http_urls_are_rejected(db_session, api_client, bad_url):
    competitor = await alpha_competitor(db_session)
    response = await api_client.get("/api/exports/collection-prices", params={"competitor_id": competitor.id, "collection_url": bad_url})
    assert response.status_code == 400
    assert "absolute http(s) URL" in response.json()["detail"]


async def test_foreign_credentialed_and_private_hosts_are_rejected(db_session, api_client):
    competitor = await alpha_competitor(db_session)
    for url in (
        "https://attacker.example/collections/x",
        "https://user:pass@alpha.example/collections/x",
        "https://alpha.example:8443/collections/x",
    ):
        response = await api_client.get("/api/exports/collection-prices", params={"competitor_id": competitor.id, "collection_url": url})
        assert response.status_code == 400
    local = await make_competitor(db_session, "Local", "http://127.0.0.1", scrape_type="shopify_json")
    await db_session.commit()
    response = await api_client.get("/api/exports/collection-prices", params={"competitor_id": local.id, "collection_url": "http://127.0.0.1/collections/x"})
    assert response.status_code == 400
    assert "local or private" in response.json()["detail"]


async def test_www_collection_url_is_allowed_for_the_selected_competitor(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_acquisition(monkeypatch, acquisition(shopify_fixture()))
    response = await api_client.get(
        "/api/exports/collection-prices",
        params={"competitor_id": competitor.id, "collection_url": "https://www.alpha.example/collections/murder-mystery-2"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("max_pages,status", [(0, 422), (21, 422), (1, 200), (20, 200)])
async def test_max_pages_bounds_and_payload_forwarding(db_session, api_client, monkeypatch, max_pages, status):
    competitor = await alpha_competitor(db_session)
    captured = patch_export_acquisition(monkeypatch, acquisition(shopify_fixture()))
    response = await export(api_client, competitor.id, max_pages=max_pages)
    assert response.status_code == status
    if status == 200:
        assert captured["kwargs"]["max_pages"] == max_pages
        config = captured["competitor"]["selector_config"]
        assert config["discover_collections"] is False
        assert config["include_all_products"] is False
        assert config["collection_handles"] == ["murder-mystery-2"]


@pytest.mark.parametrize("fmt,content_type,filename", [
    ("csv", "text/csv", "alpha-store-murder-mystery-2-prices.csv"),
    ("jsonl", "application/x-ndjson", "alpha-store-murder-mystery-2-prices.jsonl"),
    ("json", "application/json", "alpha-store-murder-mystery-2-prices.json"),
])
async def test_default_file_formats_and_filenames_remain_compatible(db_session, api_client, monkeypatch, fmt, content_type, filename):
    competitor = await alpha_competitor(db_session)
    patch_export_acquisition(monkeypatch, acquisition(shopify_fixture()))
    response = await export(api_client, competitor.id, format=fmt)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert provenance(response)["x-market-monitor-export-source"] == "live"
    if fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert list(rows[0]) == ["competitor_name", "competitor_base_url", "collection_url", "category", "title", "price", "currency", "stock_status", "sku", "external_id", "product_url", "image_url", "scraped_at"]
        assert [row["title"] for row in rows] == ["Chill Knife", "Corrupted Batwing", "Priceless Relic"]
        assert rows[1]["price"] == "25.5"
    elif fmt == "jsonl":
        rows = [json.loads(line) for line in response.text.splitlines()]
        assert len(rows) == 3 and rows[1]["price"] == 25.5 and rows[2]["price"] is None
    else:
        body = response.json()
        assert set(body) == {"competitor", "collection_url", "products_count", "items"}
        assert body["products_count"] == 3


async def test_optional_provenance_is_versioned_not_a_default_schema_change(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_acquisition(monkeypatch, acquisition(shopify_fixture()))
    response = await export(api_client, competitor.id, format="json", include_provenance=True)
    body = response.json()
    assert body["provenance"]["source"] == "live"
    assert body["items"][0]["observed_at"] == OBSERVED_AT.isoformat()
    assert response.headers["x-market-monitor-export-provenance-version"] == "1"


async def test_unicode_missing_fields_and_numeric_prices_survive_serialization(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_acquisition(monkeypatch, acquisition([
        observation('Élite, "Knife"', price=12.34, currency="EUR", url="https://alpha.example/products/elite"),
    ]))
    response = await export(api_client, competitor.id, format="csv")
    row = next(csv.DictReader(io.StringIO(response.text)))
    assert row["title"] == 'Élite, "Knife"'
    assert row["price"] == "12.34"
    assert row["sku"] == ""
    assert row["image_url"] == ""


# Live truth ---------------------------------------------------------------

async def test_live_complete_exports_coherent_observations_with_truthful_headers(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_acquisition(monkeypatch, acquisition(shopify_fixture(), pages=2))
    response = await export(api_client, competitor.id, format="json")
    headers = provenance(response)
    assert response.status_code == 200
    assert headers["x-market-monitor-export-requested-mode"] == "live"
    assert headers["x-market-monitor-export-source"] == "live"
    assert headers["x-market-monitor-export-completeness"] == "complete"
    assert headers["x-market-monitor-export-pages-fetched"] == "2"
    assert headers["x-market-monitor-export-observation-completed-at"] == OBSERVED_AT.isoformat()
    assert response.json()["items"][1]["product_url"] == "https://alpha.example/products/corrupted-batwing"


async def test_live_partial_is_downloadable_and_never_claimed_complete(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    products = [
        observation(f"Shopify Item {number}", price=number + 0.25, url=f"https://alpha.example/products/{number}")
        for number in range(1_250)
    ]
    patch_export_acquisition(monkeypatch, acquisition(products, completeness="partial", pages=5, page_cap_reached=True))
    response = await export(api_client, competitor.id, format="json")
    headers = provenance(response)
    assert response.status_code == 200
    assert response.json()["products_count"] == 1_250
    assert headers["x-market-monitor-export-completeness"] == "partial"
    assert headers["x-market-monitor-export-page-cap-reached"] == "true"
    assert "Pagination cap" in headers["x-market-monitor-export-safe-reason"]


async def test_live_partial_request_failure_remains_explicitly_partial(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_acquisition(monkeypatch, acquisition(
        shopify_fixture()[:1],
        completeness="partial",
        reason="One or more catalog source requests failed; absence inference disabled",
    ))
    response = await export(api_client, competitor.id, format="json")
    assert response.status_code == 200
    assert provenance(response)["x-market-monitor-export-completeness"] == "partial"
    assert "source requests failed" in provenance(response)["x-market-monitor-export-safe-reason"]


async def test_live_suspicious_empty_never_substitutes_cached_products(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    await make_product(db_session, competitor, "Stale Batwing", price="99.99", category="Murder Mystery 2", url="https://alpha.example/products/stale-batwing")
    await db_session.commit()
    patch_export_acquisition(monkeypatch, acquisition([], completeness="suspicious_empty", pages=1))
    response = await export(api_client, competitor.id, format="json")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert provenance(response)["x-market-monitor-export-source"] == "live"
    assert provenance(response)["x-market-monitor-export-completeness"] == "suspicious_empty"


async def test_live_failure_is_safe_structured_error_and_never_falls_back(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    await make_product(db_session, competitor, "Stale Batwing", price="99.99", category="Murder Mystery 2", url="https://alpha.example/products/stale-batwing")
    await db_session.commit()
    patch_export_acquisition(monkeypatch, AcquisitionFailure("temporary_network", "Storefront request timed out", True))
    response = await export(api_client, competitor.id, format="json")
    body = response.json()["detail"]
    assert response.status_code == 502
    assert body["code"] == "live_acquisition_failed"
    assert body["provenance"]["source"] == "live"
    assert body["provenance"]["completeness"] == "failed"
    assert "timed out" not in json.dumps(body).lower()


async def test_export_remains_read_only(db_session, api_client, monkeypatch):
    from tests.critical.factories import counts
    competitor = await alpha_competitor(db_session)
    patch_export_acquisition(monkeypatch, acquisition(shopify_fixture()))
    await export(api_client, competitor.id)
    assert await counts(db_session, competitor.id) == {"products": 0, "active_products": 0, "snapshots": 0, "events": 0, "runs": 0}


# Explicit cache -----------------------------------------------------------

async def test_explicit_cached_mode_returns_stored_rows_with_lineage_and_cycle_state(db_session, api_client):
    competitor = await alpha_competitor(db_session)
    run = await make_complete_run(db_session, competitor)
    product = await make_product(db_session, competitor, "Stored Batwing", price="19.99", category="Murder Mystery 2", url="https://alpha.example/products/stored-batwing")
    product.last_observed_at = OBSERVED_AT
    product.last_observed_run_id = run.id
    await db_session.commit()
    response = await export(api_client, competitor.id, mode="cached", format="json", include_provenance=True)
    body = response.json()
    assert response.status_code == 200
    assert body["items"][0]["title"] == "Stored Batwing"
    assert body["items"][0]["observed_at"] == OBSERVED_AT.isoformat()
    assert body["items"][0]["observed_run_id"] == run.id
    assert body["provenance"]["source"] == "cached"
    assert body["provenance"]["latest_complete_run_id"] == run.id
    assert body["provenance"]["cached_coverage_basis"]


async def test_cached_legacy_rows_are_honest_and_no_stored_data_is_explicit(db_session, api_client):
    competitor = await alpha_competitor(db_session)
    legacy = await make_product(db_session, competitor, "Legacy Batwing", price="19.99", category="Murder Mystery 2", url="https://alpha.example/products/legacy-batwing")
    legacy.last_observed_at = None
    legacy.last_observed_run_id = None
    await db_session.commit()
    response = await export(api_client, competitor.id, mode="cached", format="json", include_provenance=True)
    assert response.status_code == 200
    assert response.json()["provenance"]["coverage_state"] == "unknown"
    assert response.json()["provenance"]["degraded_or_legacy_row_count"] == 1

    empty = await make_competitor(db_session, "Empty", "https://empty.example", scrape_type="shopify_json")
    await db_session.commit()
    response = await api_client.get("/api/exports/collection-prices", params={"competitor_id": empty.id, "collection_url": "https://empty.example/collections/murder-mystery-2", "mode": "cached"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "cached_export_unavailable"
