"""
C2 — Collection Export regression coverage.

Exercises /api/exports/collection-prices end to end against a real database,
with the network-facing scraper replaced by deterministic fixtures.

Includes explicit characterisation of the saved-data fallback, which is a
product risk: the UI presents this as a live scrape, but when the live scrape
returns nothing the endpoint silently serves stored products instead, with
nothing in the payload or headers distinguishing the two. See
docs/DAILY_CRITICAL_WORKFLOWS.md "Export provenance".
"""
from __future__ import annotations

import csv
import io
import json

import pytest

from tests.conftest import requires_db
from tests.critical.factories import make_competitor, make_product, observation

pytestmark = [pytest.mark.critical, requires_db]

COLLECTION_URL = "https://alpha.example/collections/murder-mystery-2"


def patch_export_scraper(monkeypatch, result):
    """
    Replace the scraper used by the export route.

    `app.api.exports` does `from app.services.scraper import scrape_competitor`
    at module level, so the name must be patched in the exports module.
    """
    captured = {}

    async def fake_scrape(competitor, **kwargs):
        captured["competitor"] = competitor
        captured["kwargs"] = kwargs
        if isinstance(result, Exception):
            raise result
        return result

    import app.api.exports as exports_module

    monkeypatch.setattr(exports_module, "scrape_competitor", fake_scrape)
    return captured


def shopify_fixture() -> list[dict]:
    """A typical Shopify collection result, in the scraper's current dict shape."""
    return [
        observation(
            "Corrupted Batwing", price=25.50, currency="USD",
            url="https://alpha.example/products/corrupted-batwing",
            image_url="https://cdn.shopify.com/x/corrupted-batwing.png",
            stock_status="in_stock", sku="MM2-CB-01", external_id="111:222",
            category="Murder Mystery 2",
        ),
        observation(
            "Chill Knife", price=4.00, currency="USD",
            url="https://alpha.example/products/chill-knife",
            image_url="https://cdn.shopify.com/x/chill-knife.png",
            stock_status="out_of_stock", sku="MM2-CK-01", external_id="333:444",
            category="Murder Mystery 2",
        ),
        observation(
            "Priceless Relic", price=None, currency="USD",
            url="https://alpha.example/products/priceless-relic",
            stock_status="unknown", category="Murder Mystery 2",
        ),
    ]


async def alpha_competitor(session):
    competitor = await make_competitor(
        session, "Alpha Store", "https://alpha.example", scrape_type="shopify_json"
    )
    await session.commit()
    return competitor


async def export(api_client, competitor_id, **params):
    query = {"competitor_id": competitor_id, "collection_url": COLLECTION_URL, **params}
    return await api_client.get("/api/exports/collection-prices", params=query)


# ── Validation ────────────────────────────────────────────────────────────────

async def test_unknown_competitor_returns_404(db_session, api_client):
    resp = await export(api_client, 999999)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Competitor not found"


@pytest.mark.parametrize(
    "bad_url",
    [
        "not-a-url",
        "ftp://alpha.example/collections/x",
        "/collections/relative-only",
        "javascript:alert(1)",
    ],
)
async def test_non_absolute_or_non_http_urls_are_rejected(
    db_session, api_client, bad_url
):
    competitor = await alpha_competitor(db_session)
    resp = await api_client.get(
        "/api/exports/collection-prices",
        params={"competitor_id": competitor.id, "collection_url": bad_url},
    )
    assert resp.status_code == 400
    assert "absolute http(s) URL" in resp.json()["detail"]


async def test_foreign_host_is_rejected(db_session, api_client):
    """SSRF guard: the collection must belong to the selected competitor."""
    competitor = await alpha_competitor(db_session)
    resp = await api_client.get(
        "/api/exports/collection-prices",
        params={
            "competitor_id": competitor.id,
            "collection_url": "https://attacker.example/collections/x",
        },
    )
    assert resp.status_code == 400
    assert "must belong to the selected competitor" in resp.json()["detail"]


async def test_www_prefix_is_accepted_as_same_host(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture())

    resp = await api_client.get(
        "/api/exports/collection-prices",
        params={
            "competitor_id": competitor.id,
            "collection_url": "https://www.alpha.example/collections/murder-mystery-2",
        },
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("max_pages,expected_status", [(0, 422), (21, 422), (1, 200), (20, 200)])
async def test_max_pages_bounds(
    db_session, api_client, monkeypatch, max_pages, expected_status
):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture())

    resp = await export(api_client, competitor.id, max_pages=max_pages)
    assert resp.status_code == expected_status


async def test_max_pages_is_forwarded_to_the_scraper(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    captured = patch_export_scraper(monkeypatch, shopify_fixture())

    await export(api_client, competitor.id, max_pages=7)

    assert captured["kwargs"]["max_pages"] == 7
    # The export path narrows the scrape to one collection and shortens timeouts.
    config = captured["competitor"]["selector_config"]
    assert config["discover_collections"] is False
    assert config["include_all_products"] is False
    assert config["request_timeout_seconds"] == 8
    assert config["collection_handles"] == ["murder-mystery-2"]
    assert captured["competitor"]["listing_urls"] == [COLLECTION_URL]


# ── Formats and field correctness ─────────────────────────────────────────────

async def test_csv_export_fields_and_values(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture())

    resp = await export(api_client, competitor.id, format="csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert [r["title"] for r in rows] == [
        "Chill Knife", "Corrupted Batwing", "Priceless Relic"
    ], "rows are sorted by lowercased title"

    batwing = next(r for r in rows if r["title"] == "Corrupted Batwing")
    assert batwing["price"] == "25.5"
    assert batwing["currency"] == "USD"
    assert batwing["stock_status"] == "in_stock"
    assert batwing["product_url"] == "https://alpha.example/products/corrupted-batwing"
    assert batwing["image_url"] == "https://cdn.shopify.com/x/corrupted-batwing.png"
    assert batwing["category"] == "Murder Mystery 2"
    assert batwing["sku"] == "MM2-CB-01"
    assert batwing["external_id"] == "111:222"
    assert batwing["competitor_name"] == "Alpha Store"
    assert batwing["competitor_base_url"] == "https://alpha.example"
    assert batwing["collection_url"] == COLLECTION_URL
    assert batwing["scraped_at"]

    priceless = next(r for r in rows if r["title"] == "Priceless Relic")
    assert priceless["price"] == "", "a missing price serialises as empty, not 0"
    assert priceless["stock_status"] == "unknown"


async def test_jsonl_export_is_one_object_per_line(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture())

    resp = await export(api_client, competitor.id, format="jsonl")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = [line for line in resp.text.split("\n") if line]
    assert len(lines) == 3
    records = [json.loads(line) for line in lines]
    assert records[1]["title"] == "Corrupted Batwing"
    assert records[1]["price"] == 25.5
    assert records[2]["price"] is None


async def test_json_export_has_envelope(db_session, api_client, monkeypatch):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture())

    resp = await export(api_client, competitor.id, format="json")
    assert resp.status_code == 200
    body = resp.json()

    assert body["competitor"] == "Alpha Store"
    assert body["collection_url"] == COLLECTION_URL
    assert body["products_count"] == 3
    assert len(body["items"]) == 3


async def test_invalid_format_is_rejected(db_session, api_client):
    competitor = await alpha_competitor(db_session)
    resp = await export(api_client, competitor.id, format="xlsx")
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("csv", "alpha-store-murder-mystery-2-prices.csv"),
        ("jsonl", "alpha-store-murder-mystery-2-prices.jsonl"),
        ("json", "alpha-store-murder-mystery-2-prices.json"),
    ],
)
async def test_filename_is_deterministic(db_session, api_client, monkeypatch, fmt, expected):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture())

    resp = await export(api_client, competitor.id, format=fmt)
    assert resp.headers["content-disposition"] == f'attachment; filename="{expected}"'


async def test_export_does_not_persist_anything(db_session, api_client, monkeypatch):
    """
    Export is read-only with respect to market intelligence: no ScrapeRun, no
    products, no events. This means exports are invisible to the dashboard and
    to any future rate control - characterised in docs/DATA_FLOW.md Flow 8.
    """
    from tests.critical.factories import counts

    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture())

    await export(api_client, competitor.id)

    summary = await counts(db_session, competitor.id)
    assert summary == {
        "products": 0, "active_products": 0, "snapshots": 0, "events": 0, "runs": 0
    }


# ── Partial and empty results ─────────────────────────────────────────────────

async def test_partial_scrape_exports_whatever_was_returned(
    db_session, api_client, monkeypatch
):
    """
    A truncated scrape (one page of three) is indistinguishable from a complete
    one in the response. There is no completeness signal.
    """
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, shopify_fixture()[:1])

    body = (await export(api_client, competitor.id, format="json")).json()

    assert body["products_count"] == 1
    assert "partial" not in json.dumps(body).lower()
    assert "provenance" not in body


async def test_empty_scrape_with_no_saved_products_returns_empty_export(
    db_session, api_client, monkeypatch
):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, [])

    resp = await export(api_client, competitor.id, format="json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["products_count"] == 0
    assert body["items"] == []


async def test_empty_csv_export_still_has_a_header_row(
    db_session, api_client, monkeypatch
):
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, [])

    resp = await export(api_client, competitor.id, format="csv")
    lines = [line for line in resp.text.split("\r\n") if line]
    assert len(lines) == 1
    assert lines[0].startswith("competitor_name,competitor_base_url,collection_url")


# ── Saved-data fallback: the provenance risk ──────────────────────────────────

async def test_empty_scrape_silently_falls_back_to_saved_products(
    db_session, api_client, monkeypatch
):
    """
    PRODUCT RISK, CHARACTERISED (not fixed in Phase 1A).

    The UI calls this a live collection scrape. When the live scrape returns
    nothing, api/exports.py:60 substitutes previously stored products. The
    response is byte-for-byte indistinguishable from a successful live export:
    same status, same headers, same filename, same fields. The owner can act on
    month-old prices believing they are current.

    Phase 1D must add an explicit provenance contract
    (live | cached | partial | failed) - see docs/DAILY_CRITICAL_WORKFLOWS.md.
    """
    competitor = await alpha_competitor(db_session)
    await make_product(
        db_session, competitor, "Stale Batwing", price="99.99",
        category="Murder Mystery 2",
        url="https://alpha.example/products/stale-batwing",
        sku="OLD-1", external_id="999:888",
    )
    await db_session.commit()

    patch_export_scraper(monkeypatch, [])   # live scrape yields nothing

    resp = await export(api_client, competitor.id, format="json")
    body = resp.json()

    assert resp.status_code == 200
    assert body["products_count"] == 1
    assert body["items"][0]["title"] == "Stale Batwing"
    assert body["items"][0]["price"] == 99.99

    # Nothing anywhere says this is cached rather than live. Check envelope and
    # item KEYS (not values - the fixture title deliberately contains "Stale").
    assert set(body) == {"competitor", "collection_url", "products_count", "items"}
    item_keys = set(body["items"][0])
    for provenance_key in ("provenance", "source", "warning", "is_stale", "observed_at"):
        assert provenance_key not in body, f"envelope unexpectedly has {provenance_key}"
        assert provenance_key not in item_keys, f"item unexpectedly has {provenance_key}"
    assert "x-data-provenance" not in {k.lower() for k in resp.headers}

    # The filename and content-type are identical to a live export.
    assert resp.headers["content-disposition"] == (
        'attachment; filename="alpha-store-murder-mystery-2-prices.json"'
    )

    # And scraped_at is stamped with *now*, describing when the export was
    # generated - not when the price was actually observed.
    assert body["items"][0]["scraped_at"] is not None


async def test_fallback_only_returns_products_matching_the_collection(
    db_session, api_client, monkeypatch
):
    """The fallback filters saved products by collection alias, so it does not
    dump the entire catalogue."""
    competitor = await alpha_competitor(db_session)
    await make_product(db_session, competitor, "MM2 Item", price="5.00",
                       category="Murder Mystery 2",
                       url="https://alpha.example/products/mm2-item")
    await make_product(db_session, competitor, "Unrelated Pet", price="6.00",
                       category="Adopt Me",
                       url="https://alpha.example/products/unrelated-pet")
    await db_session.commit()

    patch_export_scraper(monkeypatch, [])

    body = (await export(api_client, competitor.id, format="json")).json()

    titles = [i["title"] for i in body["items"]]
    assert titles == ["MM2 Item"]


async def test_fallback_excludes_inactive_saved_products(
    db_session, api_client, monkeypatch
):
    competitor = await alpha_competitor(db_session)
    await make_product(db_session, competitor, "Retired Item", price="5.00",
                       category="Murder Mystery 2",
                       url="https://alpha.example/products/retired", active=False)
    await db_session.commit()

    patch_export_scraper(monkeypatch, [])

    body = (await export(api_client, competitor.id, format="json")).json()
    assert body["items"] == []


async def test_successful_scrape_does_not_trigger_fallback(
    db_session, api_client, monkeypatch
):
    """The fallback must only engage on an empty result, never merging stale
    rows into a good live scrape."""
    competitor = await alpha_competitor(db_session)
    await make_product(db_session, competitor, "Stale Batwing", price="99.99",
                       category="Murder Mystery 2",
                       url="https://alpha.example/products/stale-batwing")
    await db_session.commit()

    patch_export_scraper(monkeypatch, shopify_fixture())

    body = (await export(api_client, competitor.id, format="json")).json()

    titles = [i["title"] for i in body["items"]]
    assert "Stale Batwing" not in titles
    assert body["products_count"] == 3


# ── Scraper failure ───────────────────────────────────────────────────────────

async def test_scraper_exception_is_not_handled_and_returns_500(
    db_session, api_client, monkeypatch
):
    """
    BUG (documented): api/exports.py has no error handling around the live
    scrape, so a scraper exception escapes as an unhandled 500 rather than a
    structured failure. Note this is also the ONLY path where the user learns
    the scrape failed - an empty result is silently masked by the fallback.
    """
    competitor = await alpha_competitor(db_session)
    patch_export_scraper(monkeypatch, RuntimeError("upstream refused connection"))

    with pytest.raises(RuntimeError, match="upstream refused connection"):
        await export(api_client, competitor.id)
