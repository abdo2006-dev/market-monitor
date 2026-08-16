"""High-value deterministic morning workflow acceptance.

This proves the persisted chain Sync -> Search -> Export against migrated
PostgreSQL. Live Export remains a separate acquisition and is asserted not to
borrow the reconciled cached row.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.application.sync import claim_next_run, process_claimed_run, request_competitor_scan
from app.database import AsyncSessionLocal
from app.domain.acquisition import AcquisitionResult
from app.models import Product, ScrapeRun
from tests.conftest import requires_db
from tests.critical.factories import make_competitor, make_product, observation


pytestmark = [pytest.mark.acceptance, requires_db]


def acquisition(items: list[dict], *, price_time: datetime) -> AcquisitionResult:
    return AcquisitionResult(
        observations=[{**item, "observed_at": price_time} for item in items],
        strategy="deterministic_fixture",
        started_at=price_time - timedelta(seconds=1),
        completed_at=price_time,
        pages_fetched=1,
        request_count=1,
        completeness="complete",
        page_cap_reached=False,
        completeness_reason="fixture reached an explicit catalog end",
    )


async def test_morning_sync_search_cached_and_live_export_chain(
    db_session, api_client, monkeypatch
):
    competitor = await make_competitor(
        db_session,
        name="Morning Market",
        base_url="https://morning.example",
        selector_config={"discover_collections": True, "include_all_products": True},
    )
    product = await make_product(
        db_session,
        competitor,
        "Batwing",
        price="60.00",
        currency="USD",
        category="Murder Mystery 2",
        external_id="101:201",
        url="https://morning.example/products/batwing",
        last_checked_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    product_id = product.id
    product_url = product.url
    competitor_id = competitor.id
    await db_session.commit()

    request = await request_competitor_scan(db_session, competitor.id)
    await db_session.commit()
    request_id = str(request.id)
    run = next(
        run
        for run in (await db_session.execute(select(ScrapeRun))).scalars()
        if run.competitor_id == competitor.id
    )
    run_id = run.id

    async with AsyncSessionLocal() as session:
        claimed = await claim_next_run(session, worker_id="acceptance", run_id=run_id)
        await session.commit()
    assert claimed is not None

    observed_at = datetime.now(timezone.utc)
    outcome = await process_claimed_run(
        claimed,
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [
                    observation(
                        "Batwing",
                        price=45,
                        currency="USD",
                        category="Murder Mystery 2",
                        external_id="101:999",
                        url=product_url,
                    )
                ],
                price_time=observed_at,
            ),
        ),
    )
    assert outcome["status"] == "success"

    db_session.expire_all()
    reconciled = await db_session.get(Product, product_id)
    assert reconciled.current_price == Decimal("45.00")
    assert reconciled.last_observed_run_id == run_id

    suggestions = await api_client.get("/api/search/suggestions", params={"q": "Batwing"})
    assert suggestions.status_code == 200
    suggestion = suggestions.json()["items"][0]
    assert suggestion["representative_product_id"] == product_id

    comparison = await api_client.get(
        "/api/search/compare", params={"product_id": product_id}
    )
    assert comparison.status_code == 200
    body = comparison.json()
    assert body["items"][0]["product"]["current_price"] == 45.0
    assert body["items"][0]["trust"]["reliable"] is True
    assert body["market_summary"]["currencies"][0]["lowest_reliable_price"] == 45.0

    cached = await api_client.get(
        "/api/exports/collection-prices",
        params={
                "competitor_id": competitor_id,
            "collection_url": "https://morning.example/collections/murder-mystery-2",
            "format": "json",
            "mode": "cached",
            "include_provenance": "true",
        },
    )
    assert cached.status_code == 200
    cached_body = cached.json()
    assert cached_body["items"][0]["price"] == 45.0
    assert cached_body["provenance"]["source"] == "cached"
    assert cached_body["items"][0]["observed_run_id"] == run_id

    live_result = acquisition(
        [
            observation(
                "Batwing",
                price=44,
                currency="USD",
                category="Murder Mystery 2",
                external_id="101:777",
                url=product_url,
            )
        ],
        price_time=datetime.now(timezone.utc),
    )

    async def fake_live(_payload, **_options):
        return live_result

    monkeypatch.setattr("app.api.exports.acquire_collection", fake_live)
    live = await api_client.get(
        "/api/exports/collection-prices",
        params={
            "competitor_id": competitor_id,
            "collection_url": "https://morning.example/collections/murder-mystery-2",
            "format": "json",
            "mode": "live",
            "include_provenance": "true",
        },
    )
    assert live.status_code == 200
    live_body = json.loads(live.text)
    assert live_body["items"][0]["price"] == 44
    assert live_body["provenance"]["source"] == "live"
    assert live_body["items"][0]["observed_run_id"] is None

    request_status = await api_client.get(f"/api/sync/requests/{request_id}")
    assert request_status.status_code == 200
    assert request_status.json()["status"] == "success"
