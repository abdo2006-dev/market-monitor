from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.domain.acquisition import AcquisitionFailure, acquire_catalog


def preview_competitor(state: str) -> dict:
    return {
        "base_url": "https://fixture.preview.invalid",
        "scrape_type": "shopify_json",
        "selector_config": {
            "preview_demo": True,
            "preview_export_state": state,
            "preview_fixture_price": 84.99,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "completeness", "count", "page_cap"),
    [
        ("complete", "complete", 3, False),
        ("partial", "partial", 2, True),
        ("suspicious_empty", "suspicious_empty", 0, False),
    ],
)
async def test_preview_fixture_acquisition_is_deterministic(
    monkeypatch, state, completeness, count, page_cap
):
    monkeypatch.setattr(settings, "PREVIEW_DEMO_MODE", True)
    monkeypatch.setattr(settings, "VERCEL_ENV", "preview")

    result = await acquire_catalog(preview_competitor(state))

    assert result.completeness == completeness
    assert result.product_count == count
    assert result.page_cap_reached is page_cap
    assert result.strategy == f"preview_fixture_{state}"


@pytest.mark.asyncio
async def test_preview_fixture_failure_is_truthful(monkeypatch):
    monkeypatch.setattr(settings, "PREVIEW_DEMO_MODE", True)
    monkeypatch.setattr(settings, "VERCEL_ENV", "preview")

    with pytest.raises(AcquisitionFailure) as failure:
        await acquire_catalog(preview_competitor("failed"))

    assert failure.value.category == "acquisition_error"
    assert failure.value.retryable is False


@pytest.mark.asyncio
async def test_preview_fixture_cannot_activate_in_production(monkeypatch):
    called = False

    async def production_adapter(_competitor, *, telemetry, **_options):
        nonlocal called
        called = True
        telemetry.update({"strategy": "production-adapter", "pages_fetched": 1})
        return [{
            "title": "Adapter result",
            "price": 1.0,
            "currency": "USD",
            "url": "https://fixture.preview.invalid/products/adapter-result",
        }]

    monkeypatch.setattr(settings, "PREVIEW_DEMO_MODE", True)
    monkeypatch.setattr(settings, "VERCEL_ENV", "production")
    monkeypatch.setattr("app.services.scraper.scrape_competitor", production_adapter)

    result = await acquire_catalog(preview_competitor("complete"))

    assert called is True
    assert result.strategy == "production-adapter"
    assert result.product_count == 1


def test_preview_seed_verifies_schema_and_fixture_ownership_before_dml():
    source = (
        Path(__file__).parents[1] / "scripts" / "seed_preview_demo.py"
    ).read_text()
    guard_call = source.index("await _guard_preview_contents(session)")
    first_delete = source.index("delete(Competitor)")

    assert guard_call < first_delete
    assert 'EXPECTED_SCHEMA_HEAD = "0005_durable_sync_lifecycle"' in source
    assert "EXPECTED_PREVIEW_COMPETITORS = 7" in source
    assert "PREVIEW_ALLOW_INITIALIZE_EMPTY" in source
    assert "database is not the isolated preview fixture set" in source
