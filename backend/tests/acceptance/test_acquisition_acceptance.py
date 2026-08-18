from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.diagnostics.coverage_registry import LIVE_COMPETITORS
from app.domain.acquisition import INTERNAL_OBSERVED_AT_KEY, AcquisitionFailure, acquire_catalog
from app.domain.product_identity import canonicalize_product_url, product_identity_key
from app.services import scraper


pytestmark = pytest.mark.acceptance
FIXTURES = Path(__file__).parents[1] / "fixtures" / "acquisition"


def shopify_product(index: int) -> dict:
    raw = json.loads((FIXTURES / "shopify" / "product.json").read_text())
    raw["id"] = 1000 + index
    raw["handle"] = f"fixture-{index}"
    raw["title"] = f"Fixture {index}"
    raw["variants"][0]["id"] = 2000 + index
    return raw


class Response:
    def __init__(self, payload=None, *, status=200, text=""):
        self.payload = payload
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, **_kwargs):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    async def text(self):
        return self._text


class SequencedSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **_kwargs):
        self.calls.append(("GET", url))
        return self.responses.pop(0)

    def post(self, url, **_kwargs):
        self.calls.append(("POST", url))
        return self.responses.pop(0)


def assert_raw_observation_contract(item: dict, *, scrape_type="shopify_json") -> None:
    assert item["title"].strip()
    assert canonicalize_product_url(item["url"], scrape_type).startswith("https://")
    assert item["price"] is None or item["price"] >= 0
    assert len(item["currency"]) == 3
    assert item["stock_status"] in {"in_stock", "out_of_stock", "unknown"}
    assert isinstance(item[INTERNAL_OBSERVED_AT_KEY], datetime)
    assert item[INTERNAL_OBSERVED_AT_KEY].tzinfo is not None


@pytest.mark.asyncio
async def test_shopify_exact_final_partial_page_proves_complete_pagination():
    pages = [
        Response({"products": [shopify_product(index) for index in range(250)]}),
        Response({"products": [shopify_product(250 + index) for index in range(3)]}),
    ]
    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await scraper._scrape_shopify_json_targets_aiohttp(
        SequencedSession(pages),
        "https://store.example.invalid",
        [{"url": "https://store.example.invalid/products.json", "category": None}],
        100,
        telemetry,
    )
    assert len(products) == 253
    assert telemetry["pages_fetched"] == 2
    assert telemetry["page_cap_reached"] is False
    assert all(INTERNAL_OBSERVED_AT_KEY in item for item in products)


@pytest.mark.asyncio
async def test_shopify_full_page_followed_by_empty_proves_completion():
    session = SequencedSession([
        Response({"products": [shopify_product(index) for index in range(250)]}),
        Response({"products": []}),
    ])
    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await scraper._scrape_shopify_json_targets_aiohttp(
        session,
        "https://store.example.invalid",
        [{"url": "https://store.example.invalid/products.json", "category": None}],
        100,
        telemetry,
    )
    assert len(products) == 250
    assert len(session.calls) == 2
    assert telemetry["page_cap_reached"] is False


@pytest.mark.asyncio
async def test_shopify_many_page_catalog_runs_until_real_end():
    responses = [
        Response({"products": [shopify_product(page * 250 + index) for index in range(250)]})
        for page in range(7)
    ]
    responses.append(Response({"products": [shopify_product(1750 + index) for index in range(17)]}))
    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await scraper._scrape_shopify_json_targets_aiohttp(
        SequencedSession(responses),
        "https://store.example.invalid",
        [{"url": "https://store.example.invalid/products.json", "category": None}],
        100,
        telemetry,
    )
    assert len(products) == 1767
    assert telemetry["pages_fetched"] == 8
    assert telemetry["page_cap_reached"] is False


@pytest.mark.asyncio
async def test_shopify_safety_cap_duplicate_page_and_mid_catalog_failure_are_partial_evidence(monkeypatch):
    full = [shopify_product(index) for index in range(250)]

    cap_telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    capped = await scraper._scrape_shopify_json_targets_aiohttp(
        SequencedSession([Response({"products": full})]),
        "https://store.example.invalid",
        [{"url": "https://store.example.invalid/products.json", "category": None}],
        1,
        cap_telemetry,
    )
    assert len(capped) == 250
    assert cap_telemetry["page_cap_reached"] is True

    duplicate_telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    duplicated = await scraper._scrape_shopify_json_targets_aiohttp(
        SequencedSession([Response({"products": full}), Response({"products": full})]),
        "https://store.example.invalid",
        [{"url": "https://store.example.invalid/products.json", "category": None}],
        100,
        duplicate_telemetry,
    )
    assert len(duplicated) == 250
    assert duplicate_telemetry["failure_category"] == "duplicate_pagination"
    assert duplicate_telemetry["warnings"] == ["duplicate_catalog_page"]

    failure_telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    partial = await scraper._scrape_shopify_json_targets_aiohttp(
        SequencedSession([Response({"products": full}), Response(status=503)]),
        "https://store.example.invalid",
        [{"url": "https://store.example.invalid/products.json", "category": None}],
        100,
        failure_telemetry,
    )
    assert len(partial) == 250
    assert failure_telemetry["failure_category"] == "temporary_network"

    async def fake_scrape(_competitor, telemetry, **_kwargs):
        telemetry.update(failure_telemetry, strategy="shopify_products_json_aiohttp")
        return partial

    monkeypatch.setattr(scraper, "scrape_competitor", fake_scrape)
    result = await acquire_catalog({"scrape_type": "shopify_json", "selector_config": {}})
    assert result.completeness == "partial"


@pytest.mark.asyncio
async def test_shopify_malformed_response_fails_safely_without_secret_text(monkeypatch):
    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await scraper._scrape_shopify_json_targets_aiohttp(
        SequencedSession([Response({"products": "not-a-list", "token": "must-not-leak"})]),
        "https://store.example.invalid",
        [{"url": "https://store.example.invalid/products.json", "category": None}],
        100,
        telemetry,
    )
    assert products == []
    assert telemetry["failure_category"] == "malformed_response"
    assert "token" not in telemetry["failure_message"].lower()

    async def fake_scrape(_competitor, telemetry, **_kwargs):
        telemetry.update(
            failure_category="malformed_response",
            failure_message="Catalog response was malformed",
            failure_retryable=False,
        )
        return []

    monkeypatch.setattr(scraper, "scrape_competitor", fake_scrape)
    with pytest.raises(AcquisitionFailure) as caught:
        await acquire_catalog({"scrape_type": "shopify_json", "selector_config": {}})
    assert "must-not-leak" not in caught.value.safe_message


@pytest.mark.asyncio
async def test_storefront_graphql_root_products_cursor_contract():
    payload = json.loads((FIXTURES / "shopify" / "storefront_graphql.json").read_text())
    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await scraper._scrape_shopify_storefront_graphql(
        SequencedSession([Response(payload)]),
        "https://store.example.invalid",
        {
            "storefront_graphql": {
                "shop_domain": "fixture.myshopify.com",
                "access_token": "redacted-fixture-value",
                "collection_handles": [],
            }
        },
        max_pages=100,
        telemetry=telemetry,
    )
    assert len(products) == 1
    assert_raw_observation_contract(products[0])
    assert telemetry["strategy"] == "shopify_storefront_graphql"
    assert telemetry["page_cap_reached"] is False


@pytest.mark.asyncio
async def test_storefront_graphql_root_products_respects_page_ceiling():
    payload = json.loads((FIXTURES / "shopify" / "storefront_graphql.json").read_text())
    payload["data"]["products"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "fixture-next-cursor",
    }
    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await scraper._scrape_shopify_storefront_graphql(
        SequencedSession([Response(payload)]),
        "https://store.example.invalid",
        {
            "storefront_graphql": {
                "shop_domain": "fixture.myshopify.com",
                "access_token": "redacted-fixture-value",
                "collection_handles": [],
            }
        },
        max_pages=1,
        telemetry=telemetry,
    )
    assert len(products) == 1
    assert telemetry["pages_fetched"] == 1
    assert telemetry["page_cap_reached"] is True


def test_vite_storefront_config_discovery_is_platform_level_and_narrow():
    assets = (
        "const endpoint=`https://fixture-store.myshopify.com/api/2025-07/graphql.json`,"
        "publicToken=`fixture_public_token_1234567890`;"
        "fetch(endpoint,{headers:{\"X-Shopify-Storefront-Access-Token\":publicToken}})"
    )
    config = scraper._storefront_graphql_config_from_assets(
        "https://store.example.invalid",
        {},
        assets,
        [],
        default_max_products=25_000,
    )
    assert config == {
        "shop_domain": "fixture-store.myshopify.com",
        "access_token": "fixture_public_token_1234567890",
        "api_version": "2025-07",
        "collection_handles": [],
        "max_products": 25_000,
    }
    assert scraper._storefront_access_token_from_assets(
        "const unrelated=`fixture_public_token_1234567890`"
    ) is None


@pytest.mark.asyncio
async def test_shopify_sitemap_fallback_contract():
    sitemap = (FIXTURES / "shopify" / "sitemap.xml").read_text()
    product_page = (FIXTURES / "shopify" / "product_page.html").read_text()
    session = SequencedSession([
        Response(text=sitemap),
        Response(text=product_page),
    ])
    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await scraper._scrape_sitemap_product_pages(
        session,
        "https://store.example.invalid",
        {"sitemap_concurrency": 1, "max_sitemap_products": 50},
        max_pages=100,
        telemetry=telemetry,
    )
    assert len(products) == 1
    assert_raw_observation_contract(products[0])
    assert telemetry["strategy"] == "shopify_sitemap_product_pages"


@pytest.mark.asyncio
async def test_salla_cursor_pagination_and_contract(monkeypatch):
    page = json.loads((FIXTURES / "salla" / "page.json").read_text())
    first = json.loads(json.dumps(page))
    first["cursor"]["next"] = "https://salla.example.invalid/api/v1/products?page=2"
    second = json.loads(json.dumps(page))
    second["data"][0]["id"] = 302
    second["data"][0]["url"] = "https://salla.example.invalid/en/batwing-2/p302"
    session = SequencedSession([Response(first), Response(second)])

    class SessionFactory:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", SessionFactory)
    monkeypatch.setattr(scraper, "_aiohttp_connector", lambda _aiohttp: None)
    telemetry = {}
    products = await scraper.scrape_salla_json(
        {
            "base_url": "https://salla.example.invalid",
            "listing_urls": [],
            "selector_config": {"category_ids": ["123"], "currency": "USD"},
        },
        max_pages=100,
        telemetry=telemetry,
    )
    assert len(products) == 2
    assert all(item["external_id"] in {"301", "302"} for item in products)
    assert all(item["currency"] == "USD" for item in products)
    assert all(INTERNAL_OBSERVED_AT_KEY in item for item in products)
    assert telemetry["page_cap_reached"] is False


def test_generic_fixture_contract_and_identity_semantics():
    item = json.loads((FIXTURES / "generic" / "card.json").read_text())
    stamped = scraper._stamp_observation(item)
    assert_raw_observation_contract(stamped, scrape_type="generic_selector")
    assert product_identity_key(stamped["external_id"]) is None


def test_canonical_live_registry_is_exact_unique_and_contains_no_secrets():
    assert [item.name for item in LIVE_COMPETITORS] == [
        "Zyron", "Bloxloot", "TubbysTubby", "Bloxshop", "BloxyBarn", "MM2Cheap",
        "Shopbloxs", "Luger.GG", "BuyBlox", "PetPatch.GG", "BloxCrew", "Bloxy Store",
    ]
    assert len({item.base_url for item in LIVE_COMPETITORS}) == 12
    serialized = json.dumps([item.acquisition_payload() for item in LIVE_COMPETITORS])
    for forbidden in ("access_token", "cookie", "database_url", "webhook"):
        assert forbidden not in serialized.lower()
    assert all(item.expected_non_empty for item in LIVE_COMPETITORS)


@pytest.mark.asyncio
async def test_live_runner_is_database_free_and_never_emits_raw_exception_text(monkeypatch):
    from app.diagnostics import live_coverage

    source = Path(live_coverage.__file__).read_text()
    assert "app.models" not in source
    assert "AsyncSession" not in source

    async def unsafe_failure(*_args, **_kwargs):
        raise RuntimeError("token=secret-value database=private-host")

    monkeypatch.setattr(live_coverage, "acquire_catalog", unsafe_failure)
    result = await live_coverage.run_one(
        LIVE_COMPETITORS[0], max_pages=1, timeout_seconds=2, previous_count=None
    )
    serialized = json.dumps(result.__dict__)
    assert result.result == "FAILED"
    assert result.failure_category == "runtimeerror"
    assert "secret-value" not in serialized
    assert "private-host" not in serialized
