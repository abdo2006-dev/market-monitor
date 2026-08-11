"""Phase 1B.2 durable Sync lifecycle, completeness, freshness, and API gates."""

from __future__ import annotations

import asyncio
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.application.sync import (
    claim_next_run,
    get_competitor_freshness,
    get_request_status,
    process_claimed_run,
    recover_expired_leases,
    request_all_competitor_scans,
    request_competitor_scan,
)
from app.database import AsyncSessionLocal
from app.domain.acquisition import (
    INTERNAL_OBSERVED_AT_KEY,
    AcquisitionResult,
    acquire_catalog,
)
from app.models import Event, Product, ProductSnapshot, ScrapeRun, SyncRequest, SyncRequestRun
from tests.conftest import requires_db
from tests.critical.factories import make_competitor, make_product, observation


pytestmark = [pytest.mark.critical, requires_db]
BASE = datetime(2026, 8, 11, 5, 0, tzinfo=timezone.utc)


def acquisition(
    observations: list[dict],
    *,
    completeness: str = "complete",
    observed_at: datetime = BASE,
    completed_at: datetime | None = None,
    page_cap: bool = False,
) -> AcquisitionResult:
    completed_at = completed_at or observed_at
    return AcquisitionResult(
        observations=[
            {**item, "observed_at": item.get("observed_at", observed_at)}
            for item in observations
        ],
        strategy="fixture",
        started_at=min(observed_at, completed_at) - timedelta(seconds=1),
        completed_at=completed_at,
        pages_fetched=5 if page_cap else 1,
        request_count=5 if page_cap else 1,
        completeness=completeness,
        page_cap_reached=page_cap,
        completeness_reason="fixture coverage evidence",
    )


async def claim(run_id: int | None = None, *, now: datetime | None = None):
    async with AsyncSessionLocal() as session:
        value = await claim_next_run(
            session, worker_id="test-worker", run_id=run_id, now=now
        )
        await session.commit()
        return value


async def requested_run(session, competitor_id: int) -> tuple[SyncRequest, ScrapeRun]:
    request = await request_competitor_scan(session, competitor_id)
    await session.commit()
    payload = await get_request_status(session, request.id)
    return request, await session.get(ScrapeRun, payload["runs"][0]["run_id"])


async def test_request_queued_and_repeated_manual_request_reuses_non_terminal_run(db_session):
    competitor = await make_competitor(db_session)
    await db_session.commit()

    first = await request_competitor_scan(db_session, competitor.id)
    await db_session.commit()
    second = await request_competitor_scan(db_session, competitor.id)
    await db_session.commit()

    links = (
        await db_session.execute(select(SyncRequestRun).order_by(SyncRequestRun.request_id))
    ).scalars().all()
    assert first.id != second.id
    assert len(links) == 2
    assert links[0].scrape_run_id == links[1].scrape_run_id
    run = await db_session.get(ScrapeRun, links[0].scrape_run_id)
    assert run.status == "queued"
    assert run.attempt_count == 0


async def test_overlapping_sync_all_reuses_each_competitor_run(db_session):
    first_competitor = await make_competitor(db_session, name="A")
    second_competitor = await make_competitor(
        db_session, name="B", base_url="https://second.example"
    )
    await db_session.commit()
    first = await request_all_competitor_scans(db_session)
    await db_session.commit()
    second = await request_all_competitor_scans(db_session)
    await db_session.commit()

    assert first.id != second.id
    assert (
        await db_session.execute(select(func.count(ScrapeRun.id)))
    ).scalar_one() == 2
    for competitor in (first_competitor, second_competitor):
        assert (
            await db_session.execute(
                select(func.count(ScrapeRun.id)).where(
                    ScrapeRun.competitor_id == competitor.id
                )
            )
        ).scalar_one() == 1


async def test_two_morning_recovery_invocations_are_idempotent(db_session):
    await make_competitor(db_session)
    await db_session.commit()
    key = "automatic:2026-08-11"
    first = await request_all_competitor_scans(
        db_session,
        trigger="automatic_morning",
        request_idempotency_key=key,
        local_date=date(2026, 8, 11),
    )
    await db_session.commit()
    second = await request_all_competitor_scans(
        db_session,
        trigger="automatic_morning",
        request_idempotency_key=key,
        local_date=date(2026, 8, 11),
    )
    await db_session.commit()
    assert first.id == second.id
    assert (await db_session.execute(select(func.count(ScrapeRun.id)))).scalar_one() == 1


@pytest.mark.parametrize("claim_race_iteration", range(5))
async def test_two_workers_claim_one_run_once(db_session, claim_race_iteration):
    competitor = await make_competitor(db_session)
    _, run = await requested_run(db_session, competitor.id)
    run_id = run.id

    first, second = await asyncio.gather(claim(run_id), claim(run_id))
    assert sum(value is not None for value in (first, second)) == 1
    db_session.expire_all()
    persisted = await db_session.get(ScrapeRun, run_id)
    assert persisted.status == "running"
    assert persisted.attempt_count == 1
    assert persisted.claim_token is not None


async def test_claim_success_is_atomic_and_history_has_run_lineage(db_session):
    competitor = await make_competitor(db_session)
    _, run = await requested_run(db_session, competitor.id)
    run_id = run.id
    competitor_base_url = competitor.base_url
    owned = await claim(run_id)
    result = await process_claimed_run(
        owned,
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Tracked", price=4, url=f"{competitor_base_url}/products/tracked")]
            ),
        ),
    )
    assert result["status"] == "success"
    db_session.expire_all()
    persisted = await db_session.get(ScrapeRun, run_id)
    assert persisted.completeness == "complete"
    assert persisted.terminal_at is not None
    assert persisted.claim_token is None
    snapshot = (await db_session.execute(select(ProductSnapshot))).scalar_one()
    event = (await db_session.execute(select(Event))).scalar_one()
    assert snapshot.scrape_run_id == run_id
    assert event.scrape_run_id == run_id
    assert snapshot.observed_at == BASE


async def test_complete_catalog_permits_missing_and_removal(db_session):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Missing", consecutive_misses=2,
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    _, run = await requested_run(db_session, competitor.id)
    owned = await claim(run.id)
    await process_claimed_run(
        owned, acquire=lambda _: asyncio.sleep(0, result=acquisition([]))
    )
    db_session.expire_all()
    product = await db_session.get(Product, product_id)
    assert product.active is False
    assert product.consecutive_misses == 3
    assert (await db_session.execute(select(Event.event_type))).scalar_one() == "product_removed"


@pytest.mark.parametrize("completeness", ["partial", "suspicious_empty"])
async def test_incomplete_catalog_never_increments_missing_or_removes(
    db_session, completeness
):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Protected", consecutive_misses=2,
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    _, run = await requested_run(db_session, competitor.id)
    owned = await claim(run.id)
    await process_claimed_run(
        owned,
        acquire=lambda _: asyncio.sleep(
            0, result=acquisition([], completeness=completeness)
        ),
    )
    db_session.expire_all()
    product = await db_session.get(Product, product_id)
    assert product.active is True
    assert product.consecutive_misses == 2
    assert (await db_session.execute(select(func.count(Event.id)))).scalar_one() == 0


async def test_partial_catalog_updates_observed_newer_price_but_not_unobserved_miss(db_session):
    competitor = await make_competitor(db_session)
    observed = await make_product(
        db_session, competitor, "Observed", price="10", last_checked_at=BASE - timedelta(days=1)
    )
    unobserved = await make_product(
        db_session, competitor, "Unobserved", consecutive_misses=2,
        last_checked_at=BASE - timedelta(days=1),
    )
    observed_id = observed.id
    observed_url = observed.url
    unobserved_id = unobserved.id
    _, run = await requested_run(db_session, competitor.id)
    run_id = run.id
    owned = await claim(run_id)
    await process_claimed_run(
        owned,
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Observed", price=7, url=observed_url)], completeness="partial"
            ),
        ),
    )
    db_session.expire_all()
    assert (await db_session.get(Product, observed_id)).current_price == Decimal("7.00")
    assert (await db_session.get(Product, unobserved_id)).consecutive_misses == 2
    assert (await db_session.get(ScrapeRun, run_id)).completeness == "partial"


async def test_shopify_full_fifth_page_is_conservatively_partial(monkeypatch):
    products = [
        observation(str(index), price=1, url=f"https://store.example/products/{index}")
        for index in range(1250)
    ]

    async def fake_scrape(_competitor, telemetry, **_kwargs):
        telemetry.update(
            strategy="shopify_products_json_aiohttp",
            pages_fetched=5,
            request_count=5,
            page_cap_reached=True,
        )
        return products

    import app.services.scraper as scraper_module

    monkeypatch.setattr(scraper_module, "scrape_competitor", fake_scrape)
    result = await acquire_catalog(
        {"scrape_type": "shopify_json", "selector_config": {}}
    )
    assert result.product_count == 1250
    assert result.page_cap_reached is True
    assert result.completeness == "partial"


async def test_acquisition_preserves_server_batch_times_and_ignores_public_source_time(
    monkeypatch,
):
    source_time = BASE - timedelta(days=30)

    async def fake_scrape(_competitor, telemetry, **_kwargs):
        first_time = datetime.now(timezone.utc)
        await asyncio.sleep(0.001)
        second_time = datetime.now(timezone.utc)
        telemetry.update(
            strategy="fixture", pages_fetched=2, request_count=2,
            page_cap_reached=False,
        )
        return [
            {
                **observation("First batch", price=1),
                "observed_at": source_time,
                INTERNAL_OBSERVED_AT_KEY: first_time,
            },
            {
                **observation("Second batch", price=2),
                "observed_at": source_time,
                INTERNAL_OBSERVED_AT_KEY: second_time,
            },
        ]

    import app.services.scraper as scraper_module

    monkeypatch.setattr(scraper_module, "scrape_competitor", fake_scrape)
    result = await acquire_catalog({"scrape_type": "shopify_json", "selector_config": {}})

    first_at, second_at = [item["observed_at"] for item in result.observations]
    assert result.started_at <= first_at < second_at <= result.completed_at
    assert source_time not in (first_at, second_at)
    assert INTERNAL_OBSERVED_AT_KEY not in result.observations[0]
    assert result.observation_started_at == first_at
    assert result.observation_completed_at == second_at


async def test_successful_observations_with_a_failed_source_are_partial(monkeypatch):
    async def fake_scrape(_competitor, telemetry, **_kwargs):
        telemetry.update(
            strategy="fixture",
            pages_fetched=1,
            request_count=2,
            page_cap_reached=False,
            failure_category="temporary_network",
            failure_message="safe fixture failure",
            failure_retryable=True,
        )
        return [observation("Observed", price=1)]

    import app.services.scraper as scraper_module

    monkeypatch.setattr(scraper_module, "scrape_competitor", fake_scrape)
    result = await acquire_catalog({"scrape_type": "shopify_json", "selector_config": {}})
    assert result.product_count == 1
    assert result.completeness == "partial"


async def test_shopify_graphql_pagination_metadata_marks_configured_cap_partial():
    from app.services.scraper import _scrape_shopify_storefront_graphql

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def json(self, **_kwargs):
            return {
                "data": {
                    "collection": {
                        "title": "Catalog",
                        "products": {
                            "edges": [{"node": {
                                "id": "gid://shopify/Product/1",
                                "title": "One",
                                "handle": "one",
                                "images": {"edges": []},
                                "variants": {"edges": [{"node": {
                                    "id": "gid://shopify/ProductVariant/2",
                                    "availableForSale": True,
                                    "price": {"amount": "1.00", "currencyCode": "USD"},
                                }}]},
                            }}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                        },
                    }
                }
            }

    class Session:
        def post(self, *_args, **_kwargs):
            return Response()

    telemetry = {"pages_fetched": 0, "request_count": 0, "page_cap_reached": False}
    products = await _scrape_shopify_storefront_graphql(
        Session(),
        "https://store.example",
        {"storefront_graphql": {
            "shop_domain": "store.myshopify.com",
            "access_token": "fixture-token",
            "collection_handles": ["catalog"],
            "max_products": 1,
        }},
        telemetry=telemetry,
    )
    assert len(products) == 1
    assert telemetry == {
        "pages_fetched": 1,
        "request_count": 1,
        "page_cap_reached": True,
        "strategy": "shopify_storefront_graphql",
    }


async def test_retry_then_success_and_retry_exhaustion(db_session):
    competitor = await make_competitor(db_session)
    _, retry_run = await requested_run(db_session, competitor.id)
    retry_run_id = retry_run.id
    retry_run.max_attempts = 2
    await db_session.commit()
    first_claim = await claim(retry_run_id)

    async def timeout(_):
        raise asyncio.TimeoutError()

    first = await process_claimed_run(first_claim, acquire=timeout)
    assert first["status"] == "retry_wait"
    async with AsyncSessionLocal() as session:
        persisted = await session.get(ScrapeRun, retry_run_id)
        persisted.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    second_claim = await claim(retry_run_id)
    second = await process_claimed_run(
        second_claim, acquire=lambda _: asyncio.sleep(0, result=acquisition([]))
    )
    assert second["status"] == "success"
    db_session.expire_all()
    assert (await db_session.get(ScrapeRun, retry_run_id)).attempt_count == 2

    _, failed_run = await requested_run(db_session, competitor.id)
    failed_run_id = failed_run.id
    failed_run.max_attempts = 1
    await db_session.commit()
    failed = await process_claimed_run(await claim(failed_run_id), acquire=timeout)
    assert failed["status"] == "failed"
    event = (
        await db_session.execute(
            select(Event).where(Event.scrape_run_id == failed_run_id)
        )
    ).scalar_one()
    assert event.event_type == "scrape_failed"
    assert "traceback" not in (event.event_message or "").lower()


async def test_failed_acquisition_never_changes_missing_state(db_session):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Protected from failure", consecutive_misses=2,
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    _, run = await requested_run(db_session, competitor.id)
    run_id = run.id
    run.max_attempts = 1
    await db_session.commit()

    async def timeout(_):
        raise asyncio.TimeoutError()

    outcome = await process_claimed_run(await claim(run_id), acquire=timeout)
    assert outcome["status"] == "failed"
    db_session.expire_all()
    product = await db_session.get(Product, product_id)
    assert product.active is True
    assert product.consecutive_misses == 2
    assert "product_removed" not in list(
        (await db_session.execute(select(Event.event_type))).scalars().all()
    )


async def test_failed_later_run_does_not_suppress_last_valid_observation(db_session):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Last valid", price="10",
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    product_url = product.url
    _, successful_run = await requested_run(db_session, competitor.id)
    await process_claimed_run(
        await claim(successful_run.id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Last valid", price=4, url=product_url)],
                observed_at=BASE + timedelta(minutes=1),
            ),
        ),
    )
    evidence_before_failure = (
        await db_session.execute(
            select(func.count(ProductSnapshot.id)).where(ProductSnapshot.product_id == product_id)
        )
    ).scalar_one()
    _, failed_run = await requested_run(db_session, competitor.id)
    failed_run_id = failed_run.id
    failed_run.max_attempts = 1
    await db_session.commit()

    async def timeout(_):
        raise asyncio.TimeoutError()

    assert (await process_claimed_run(await claim(failed_run_id), acquire=timeout))["status"] == "failed"
    db_session.expire_all()
    persisted = await db_session.get(Product, product_id)
    assert persisted.current_price == Decimal("4.00")
    assert persisted.last_observed_at == BASE + timedelta(minutes=1)
    assert (
        await db_session.execute(
            select(func.count(ProductSnapshot.id)).where(ProductSnapshot.product_id == product_id)
        )
    ).scalar_one() == evidence_before_failure
    assert list(
        (
            await db_session.execute(
                select(Event.event_type).where(Event.scrape_run_id == failed_run_id)
            )
        ).scalars()
    ) == ["scrape_failed"]


async def test_reprocessing_terminal_claim_cannot_duplicate_failure_event(db_session):
    competitor = await make_competitor(db_session)
    _, run = await requested_run(db_session, competitor.id)
    run_id = run.id
    run.max_attempts = 1
    await db_session.commit()
    owned = await claim(run_id)

    async def timeout(_):
        raise asyncio.TimeoutError()

    assert (await process_claimed_run(owned, acquire=timeout))["status"] == "failed"
    assert (await process_claimed_run(owned, acquire=timeout))["status"] == "claim_lost"
    assert (
        await db_session.execute(
            select(func.count(Event.id)).where(Event.scrape_run_id == run_id)
        )
    ).scalar_one() == 1


async def test_expired_lease_recovered_then_abandoned_when_attempts_exhausted(db_session):
    competitor = await make_competitor(db_session)
    _, run = await requested_run(db_session, competitor.id)
    run_id = run.id
    lease_clock = datetime.now(timezone.utc) + timedelta(seconds=1)
    owned = await claim(run_id, now=lease_clock)
    assert owned is not None
    async with AsyncSessionLocal() as session:
        result = await recover_expired_leases(session, now=lease_clock + timedelta(hours=1))
        await session.commit()
    assert result == {"recovered": 1, "abandoned": 0}
    second = await claim(run_id, now=lease_clock + timedelta(hours=1))
    assert second.claim_token != owned.claim_token

    async with AsyncSessionLocal() as session:
        persisted = await session.get(ScrapeRun, run_id)
        persisted.attempt_count = persisted.max_attempts
        persisted.lease_expires_at = lease_clock
        await session.commit()
    async with AsyncSessionLocal() as session:
        result = await recover_expired_leases(session, now=lease_clock + timedelta(hours=2))
        await session.commit()
    assert result == {"recovered": 0, "abandoned": 1}
    db_session.expire_all()
    assert (await db_session.get(ScrapeRun, run_id)).status == "abandoned"


async def test_expired_old_owner_cannot_apply_overlapping_complete_absence(db_session):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Lease fenced", consecutive_misses=1,
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    _, run = await requested_run(db_session, competitor.id)
    run_id = run.id
    first_clock = datetime.now(timezone.utc)
    old_owner = await claim(run_id, now=first_clock)
    async with AsyncSessionLocal() as session:
        await recover_expired_leases(session, now=first_clock + timedelta(hours=1))
        await session.commit()
    new_owner = await claim(run_id, now=first_clock + timedelta(hours=1))
    assert new_owner.claim_token != old_owner.claim_token

    assert (
        await process_claimed_run(
            new_owner, acquire=lambda _: asyncio.sleep(0, result=acquisition([]))
        )
    )["status"] == "success"
    assert (
        await process_claimed_run(
            old_owner, acquire=lambda _: asyncio.sleep(0, result=acquisition([]))
        )
    )["status"] == "claim_lost"
    db_session.expire_all()
    persisted = await db_session.get(Product, product_id)
    assert persisted.consecutive_misses == 2
    assert persisted.active is True


async def test_older_observation_cannot_overwrite_newer_actual_observation(db_session):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Fresh", price="10", last_checked_at=BASE - timedelta(days=1)
    )
    product_id = product.id
    product_url = product.url
    _, newer_run = await requested_run(db_session, competitor.id)
    await process_claimed_run(
        await claim(newer_run.id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Fresh", price=3, url=product_url)],
                observed_at=BASE + timedelta(minutes=10),
            ),
        ),
    )
    _, older_run = await requested_run(db_session, competitor.id)
    outcome = await process_claimed_run(
        await claim(older_run.id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Fresh", price=5, url=product_url)], observed_at=BASE
            ),
        ),
    )
    assert outcome["status"] == "stale_skipped"
    db_session.expire_all()
    assert (await db_session.get(Product, product_id)).current_price == Decimal("3.00")
    assert [
        value
        for value in (
            await db_session.execute(
                select(Event.event_type).where(Event.product_id == product_id).order_by(Event.id)
            )
        ).scalars().all()
    ] == ["price_decrease"]


async def test_earlier_five_dollar_fetch_cannot_win_by_completing_after_newer_three_dollar_fetch(
    db_session,
):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Completion must not win", price="10",
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    product_url = product.url

    # B obtained the newer $3 evidence at T3 and returned at T4.
    _, run_b = await requested_run(db_session, competitor.id)
    run_b_id = run_b.id
    run_b.started_at = BASE + timedelta(minutes=2)
    await db_session.commit()
    await process_claimed_run(
        await claim(run_b_id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Completion must not win", price=3, url=product_url)],
                observed_at=BASE + timedelta(minutes=3),
                completed_at=BASE + timedelta(minutes=4),
            ),
        ),
    )

    # A obtained $5 earlier at T1, but its stalled acquisition returned at T5.
    _, run_a = await requested_run(db_session, competitor.id)
    run_a_id = run_a.id
    run_a.started_at = BASE
    await db_session.commit()
    outcome = await process_claimed_run(
        await claim(run_a_id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Completion must not win", price=5, url=product_url)],
                observed_at=BASE + timedelta(minutes=1),
                completed_at=BASE + timedelta(minutes=5),
            ),
        ),
    )

    assert outcome["status"] == "stale_skipped"
    db_session.expire_all()
    persisted = await db_session.get(Product, product_id)
    persisted_a = await db_session.get(ScrapeRun, run_a_id)
    assert persisted.current_price == Decimal("3.00")
    assert persisted.last_observed_at == BASE + timedelta(minutes=3)
    assert persisted_a.acquisition_completed_at == BASE + timedelta(minutes=5)
    assert persisted_a.observation_completed_at == BASE + timedelta(minutes=1)


async def test_equal_observation_times_use_higher_run_id_as_tie_break(db_session):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Tie break", price="10",
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    product_url = product.url
    _, first_run = await requested_run(db_session, competitor.id)
    first_run_id = first_run.id
    await process_claimed_run(
        await claim(first_run_id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Tie break", price=5, url=product_url)], observed_at=BASE
            ),
        ),
    )
    _, second_run = await requested_run(db_session, competitor.id)
    second_run_id = second_run.id
    await process_claimed_run(
        await claim(second_run_id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Tie break", price=3, url=product_url)], observed_at=BASE
            ),
        ),
    )

    db_session.expire_all()
    persisted = await db_session.get(Product, product_id)
    assert second_run_id > first_run_id
    assert persisted.current_price == Decimal("3.00")
    assert persisted.last_observed_run_id == second_run_id


async def test_newer_observed_at_supersedes_even_with_older_run_start(db_session):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Observation wins", price="10",
        last_checked_at=BASE - timedelta(days=1),
    )
    product_id = product.id
    product_url = product.url
    _, first_run = await requested_run(db_session, competitor.id)
    await process_claimed_run(
        await claim(first_run.id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Observation wins", price=5, url=product_url)],
                observed_at=BASE,
            ),
        ),
    )
    _, second_run = await requested_run(db_session, competitor.id)
    second_run_id = second_run.id
    second_run.started_at = BASE - timedelta(days=10)
    await db_session.commit()
    await process_claimed_run(
        await claim(second_run_id),
        acquire=lambda _: asyncio.sleep(
            0,
            result=acquisition(
                [observation("Observation wins", price=3, url=product_url)],
                observed_at=BASE + timedelta(minutes=1),
            ),
        ),
    )
    db_session.expire_all()
    assert (await db_session.get(Product, product_id)).current_price == Decimal("3.00")


async def test_freshness_coverage_uses_observation_time_not_queue_order(db_session):
    competitor = await make_competitor(db_session)
    db_session.add_all([
        ScrapeRun(
            competitor_id=competitor.id,
            status="success",
            trigger="manual",
            queued_at=BASE + timedelta(hours=1),
            started_at=BASE + timedelta(hours=1),
            finished_at=BASE + timedelta(hours=1),
            terminal_at=BASE + timedelta(hours=1),
            observation_completed_at=BASE + timedelta(minutes=10),
            completeness="complete",
            attempt_count=1,
            max_attempts=3,
        ),
        ScrapeRun(
            competitor_id=competitor.id,
            status="success",
            trigger="manual",
            queued_at=BASE + timedelta(hours=2),
            started_at=BASE + timedelta(hours=2),
            finished_at=BASE + timedelta(hours=2),
            terminal_at=BASE + timedelta(hours=2),
            observation_completed_at=BASE + timedelta(minutes=5),
            completeness="partial",
            attempt_count=1,
            max_attempts=3,
        ),
    ])
    await db_session.commit()
    row = (await get_competitor_freshness(db_session))[0]
    assert row["coverage_complete"] is True
    assert row["last_complete_at"] == BASE + timedelta(minutes=10)
    assert row["latest_partial_at"] == BASE + timedelta(minutes=5)


async def test_api_accepts_durably_with_202_and_never_claims_success(
    db_session, api_client
):
    competitor = await make_competitor(db_session)
    await db_session.commit()
    response = await api_client.post(f"/api/sync/competitors/{competitor.id}")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["runs"][0]["status"] == "queued"
    assert body["runs"][0]["products_observed"] == 0
    assert response.headers["location"].endswith(body["request_id"])
    persisted = await api_client.get(f"/api/sync/requests/{body['request_id']}")
    assert persisted.status_code == 200
    assert persisted.json()["status"] == "queued"


async def test_dispatch_failure_remains_queued_and_truthful(db_session, api_client, monkeypatch):
    competitor = await make_competitor(db_session)
    await db_session.commit()

    async def fail_dispatch(request_id):
        from app.infrastructure.github_actions import _record_dispatch

        await _record_dispatch(request_id, "failed", "github_unreachable")
        return "failed"

    import app.api.sync as sync_api

    monkeypatch.setattr(sync_api, "dispatch_sync_request", fail_dispatch)
    response = await api_client.post(f"/api/sync/competitors/{competitor.id}")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["dispatch_status"] == "failed"
    assert body["dispatch_error_category"] == "github_unreachable"


async def test_worker_once_clean_exit_with_no_work(db_session):
    from app.workers.sync_worker import _main

    code = await _main(
        Namespace(
            worker_id="test-worker", run_id=None, request_id=None,
            once=True, drain=False, morning=False,
        )
    )
    assert code == 0


async def test_worker_request_exit_code_reports_terminal_failure(db_session):
    from app.workers.sync_worker import _main

    competitor = await make_competitor(db_session)
    request, run = await requested_run(db_session, competitor.id)
    run.status = "failed"
    run.completeness = "failed"
    run.terminal_at = BASE
    run.finished_at = BASE
    await db_session.commit()
    code = await _main(
        Namespace(
            worker_id="test-worker", run_id=None, request_id=str(request.id),
            once=False, drain=False, morning=False,
        )
    )
    assert code == 1
