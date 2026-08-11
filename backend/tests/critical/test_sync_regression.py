"""
C3 — Competitor price synchronisation regression coverage.

These tests exercise the REAL authoritative reconciliation path
(`workers.tasks._scrape_competitor_async` -> `services.detection.detect_changes`)
against a real PostgreSQL database, with only the network-facing scraper
replaced by a deterministic fixture.

They characterise current behaviour, including behaviour that is wrong. Where
something is wrong, the test asserts what actually happens and is marked with a
BUG comment naming the finding in docs/DAILY_CRITICAL_WORKFLOWS.md. Phase 1A does
not change sync behaviour; it pins it down so Phase 1B can change it safely.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Event, Product, ProductSnapshot, ScrapeRun
from tests.conftest import requires_db
from tests.critical.factories import (
    counts,
    event_types,
    make_competitor,
    make_product,
    observation,
)

pytestmark = [pytest.mark.critical, requires_db]


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    """Sync tests must never attempt a webhook."""
    from app.config import settings

    monkeypatch.setattr(settings, "DISCORD_NOTIFICATIONS_ENABLED", False)
    monkeypatch.setattr(settings, "DISCORD_DEFAULT_WEBHOOK_URL", None)


def patch_scraper(monkeypatch, *results):
    """
    Replace the network scraper with a scripted sequence of results.

    Each call returns the next entry. An entry may be an exception instance, in
    which case it is raised — this is how scrape failure is simulated.
    """
    calls = {"n": 0}

    async def fake_scrape(competitor, **kwargs):
        index = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        result = results[index]
        if isinstance(result, Exception):
            raise result
        return result

    import app.services.scraper as scraper_module

    monkeypatch.setattr(scraper_module, "scrape_competitor", fake_scrape)
    return calls


async def run_sync(competitor_id: int):
    from app.workers.tasks import _scrape_competitor_async

    return await _scrape_competitor_async(competitor_id)


# ── Price change ──────────────────────────────────────────────────────────────

async def test_price_decrease_updates_product_and_records_history(
    db_session, monkeypatch
):
    """
    Initial: Batwing = 5.00.  Observed: Batwing = 4.00.

    Expect: same product updated, price-decrease event, snapshot written,
    freshness advanced, no duplicate product.
    """
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Batwing", price="5.00",
        url="https://teststore.example/products/batwing",
    )
    await db_session.commit()
    original_id = product.id
    checked_before = product.last_checked_at

    patch_scraper(monkeypatch, [
        observation("Batwing", price=4.00, url="https://teststore.example/products/batwing"),
    ])
    result = await run_sync(competitor.id)

    assert result["status"] == "success"
    assert result["price_changes"] == 1

    db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
    refreshed = (
        await db_session.execute(select(Product).where(Product.id == original_id))
    ).scalar_one()

    assert refreshed.current_price == Decimal("4.00")
    assert refreshed.last_checked_at > checked_before, "freshness must advance"
    assert refreshed.active is True
    assert refreshed.consecutive_misses == 0

    summary = await counts(db_session, competitor.id)
    assert summary["products"] == 1, "must not create a duplicate product"

    assert await event_types(db_session, competitor.id) == ["price_decrease"]

    event = (
        await db_session.execute(select(Event).where(Event.competitor_id == competitor.id))
    ).scalar_one()
    assert event.old_value["price"] == 5.0
    assert event.new_value["price"] == 4.0
    assert event.new_value["diff_amount"] == -1.0
    assert event.new_value["diff_percentage"] == -20.0
    assert event.product_id == original_id

    snapshots = (
        await db_session.execute(
            select(ProductSnapshot).where(ProductSnapshot.product_id == original_id)
        )
    ).scalars().all()
    assert len(snapshots) == 1, "a change must write exactly one snapshot"
    assert snapshots[0].price == Decimal("4.00")


async def test_price_increase_produces_increase_event(db_session, monkeypatch):
    competitor = await make_competitor(db_session)
    await make_product(
        db_session, competitor, "Chill Knife", price="10.00",
        url="https://teststore.example/products/chill-knife",
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Chill Knife", price=12.50, url="https://teststore.example/products/chill-knife"),
    ])
    await run_sync(competitor.id)

    assert await event_types(db_session, competitor.id) == ["price_increase"]


# ── Unchanged ─────────────────────────────────────────────────────────────────

async def test_unchanged_product_generates_no_event_and_no_snapshot(
    db_session, monkeypatch
):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Batwing", price="5.00",
        url="https://teststore.example/products/batwing",
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Batwing", price=5.00, url="https://teststore.example/products/batwing"),
    ])
    result = await run_sync(competitor.id)

    assert result["price_changes"] == 0
    assert result["new_products"] == 0

    summary = await counts(db_session, competitor.id)
    assert summary["events"] == 0, "an unchanged product must not emit an event"
    assert summary["snapshots"] == 0, (
        "snapshots record changes, not observations - see docs/DOMAIN_MODEL.md"
    )

    db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
    refreshed = (
        await db_session.execute(select(Product).where(Product.id == product.id))
    ).scalar_one()
    assert refreshed.last_checked_at is not None


async def test_tiny_price_difference_below_configured_threshold_still_fires(
    db_session, monkeypatch
):
    """
    BUG (documented): MIN_PRICE_CHANGE_AMOUNT / MIN_PRICE_CHANGE_PERCENTAGE are
    configurable in three places and honoured in none. detection.py:217 uses a
    hardcoded 0.001 epsilon, so a 1-cent move on a $500 item emits an event.

    Asserting current behaviour so Phase 1B's fix is a visible, deliberate change.
    """
    from app.config import settings

    assert settings.MIN_PRICE_CHANGE_AMOUNT == 0.01
    assert settings.MIN_PRICE_CHANGE_PERCENTAGE == 0.1

    competitor = await make_competitor(db_session)
    await make_product(
        db_session, competitor, "Expensive Item", price="500.00",
        url="https://teststore.example/products/expensive",
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Expensive Item", price=500.005, url="https://teststore.example/products/expensive"),
    ])
    result = await run_sync(competitor.id)

    # 0.005 > 0.001 epsilon, so it fires despite being far below both thresholds.
    assert result["price_changes"] == 1


# ── New product ───────────────────────────────────────────────────────────────

async def test_new_product_created_once_with_snapshot_and_event(
    db_session, monkeypatch
):
    competitor = await make_competitor(db_session)
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation(
            "Corrupted Batwing", price=25.00,
            url="https://teststore.example/products/corrupted-batwing",
            category="Murder Mystery 2", external_id="111:222", sku="SKU1",
        ),
    ])
    result = await run_sync(competitor.id)

    assert result["new_products"] == 1
    summary = await counts(db_session, competitor.id)
    assert summary["products"] == 1
    assert summary["snapshots"] == 1, "product creation writes an initial snapshot"
    assert await event_types(db_session, competitor.id) == ["new_product"]

    product = (
        await db_session.execute(select(Product).where(Product.competitor_id == competitor.id))
    ).scalar_one()
    assert product.title == "Corrupted Batwing"
    assert product.normalized_title == "corrupted batwing"
    assert product.category == "Murder Mystery 2"
    assert product.external_id == "111:222"
    assert product.current_price == Decimal("25.00")


async def test_observation_without_url_is_skipped(db_session, monkeypatch):
    """detection.py:36 skips items with no URL. A scraper regression that loses
    URLs therefore yields an empty sync, not a crash."""
    competitor = await make_competitor(db_session)
    await db_session.commit()

    patch_scraper(monkeypatch, [observation("No URL Item", price=5.00, url="")])
    result = await run_sync(competitor.id)

    assert result["new_products"] == 0
    assert (await counts(db_session, competitor.id))["products"] == 0


# ── Removed / missing ─────────────────────────────────────────────────────────

async def test_missing_product_deactivates_only_after_three_consecutive_misses(
    db_session, monkeypatch
):
    """Characterises CONSECUTIVE_MISS_THRESHOLD = 3 (detection.py:12)."""
    competitor = await make_competitor(db_session)
    kept = await make_product(
        db_session, competitor, "Kept", price="1.00",
        url="https://teststore.example/products/kept",
    )
    vanishing = await make_product(
        db_session, competitor, "Vanishing", price="2.00",
        url="https://teststore.example/products/vanishing",
    )
    await db_session.commit()

    only_kept = [observation("Kept", price=1.00, url="https://teststore.example/products/kept")]
    patch_scraper(monkeypatch, only_kept)

    for expected_misses in (1, 2):
        await run_sync(competitor.id)
        db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
        refreshed = (
            await db_session.execute(select(Product).where(Product.id == vanishing.id))
        ).scalar_one()
        assert refreshed.consecutive_misses == expected_misses
        assert refreshed.active is True, "must not deactivate before the threshold"
        assert "product_removed" not in await event_types(db_session, competitor.id)

    await run_sync(competitor.id)
    db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
    refreshed = (
        await db_session.execute(select(Product).where(Product.id == vanishing.id))
    ).scalar_one()
    assert refreshed.consecutive_misses == 3
    assert refreshed.active is False
    assert "product_removed" in await event_types(db_session, competitor.id)

    still_there = (
        await db_session.execute(select(Product).where(Product.id == kept.id))
    ).scalar_one()
    assert still_there.active is True


async def test_returning_product_reactivates_without_event(db_session, monkeypatch):
    """A product that comes back is silently reactivated - there is no
    'product_returned' event. Characterising current behaviour."""
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Seasonal", price="3.00",
        url="https://teststore.example/products/seasonal",
        active=False, consecutive_misses=3,
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Seasonal", price=3.00, url="https://teststore.example/products/seasonal"),
    ])
    await run_sync(competitor.id)

    db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
    refreshed = (
        await db_session.execute(select(Product).where(Product.id == product.id))
    ).scalar_one()
    assert refreshed.active is True
    assert refreshed.consecutive_misses == 0
    assert await event_types(db_session, competitor.id) == []


# ── Stock transitions ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "start,observed,expected_event",
    [
        ("in_stock", "out_of_stock", "stock_out"),
        ("out_of_stock", "in_stock", "stock_in"),
    ],
)
async def test_stock_transitions(db_session, monkeypatch, start, observed, expected_event):
    competitor = await make_competitor(db_session)
    await make_product(
        db_session, competitor, "Stocky", price="7.00",
        url="https://teststore.example/products/stocky", stock_status=start,
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Stocky", price=7.00, url="https://teststore.example/products/stocky",
                    stock_status=observed),
    ])
    await run_sync(competitor.id)

    assert await event_types(db_session, competitor.id) == [expected_event]


async def test_unknown_stock_status_is_treated_as_a_transition(db_session, monkeypatch):
    """
    BUG (documented): detection.py:144 compares stock strings directly, so a
    scraper that degrades to "unknown" emits a stock_out event. Since
    `sales-trends` counts stock_out as an inferred sale, a scraper degradation
    manufactures phantom sales signals.
    """
    competitor = await make_competitor(db_session)
    await make_product(
        db_session, competitor, "Stocky", price="7.00",
        url="https://teststore.example/products/stocky", stock_status="in_stock",
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Stocky", price=7.00, url="https://teststore.example/products/stocky",
                    stock_status="unknown"),
    ])
    await run_sync(competitor.id)

    assert await event_types(db_session, competitor.id) == ["stock_out"]


# ── Identity matching ─────────────────────────────────────────────────────────

async def test_product_matched_by_external_id_when_url_changes(db_session, monkeypatch):
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Renamed", price="9.00",
        url="https://teststore.example/products/old-handle", external_id="900:901",
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Renamed", price=9.00,
                    url="https://teststore.example/products/new-handle",
                    external_id="900:901"),
    ])
    await run_sync(competitor.id)

    summary = await counts(db_session, competitor.id)
    assert summary["products"] == 1, "external_id match must not create a duplicate"

    db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
    refreshed = (
        await db_session.execute(select(Product).where(Product.id == product.id))
    ).scalar_one()
    assert refreshed.url.endswith("/new-handle")


async def test_identical_titles_at_different_urls_stay_distinct(db_session, monkeypatch):
    """Title matching was deliberately rejected (detection.py:42). Guarding it."""
    competitor = await make_competitor(db_session)
    await db_session.commit()

    patch_scraper(monkeypatch, [
        observation("Batwing", price=5.00, url="https://teststore.example/products/batwing-a"),
        observation("Batwing", price=8.00, url="https://teststore.example/products/batwing-b"),
    ])
    result = await run_sync(competitor.id)

    assert result["new_products"] == 2
    assert (await counts(db_session, competitor.id))["products"] == 2


# ── Idempotency ───────────────────────────────────────────────────────────────

async def test_repeated_identical_scan_is_idempotent(db_session, monkeypatch):
    """
    Running the same scan twice must not duplicate products or re-emit events.
    This currently HOLDS, because matching is by URL and events only fire on a
    detected difference.
    """
    competitor = await make_competitor(db_session)
    await db_session.commit()

    payload = [
        observation("Alpha", price=1.00, url="https://teststore.example/products/alpha"),
        observation("Beta", price=2.00, url="https://teststore.example/products/beta"),
    ]
    patch_scraper(monkeypatch, payload)

    first = await run_sync(competitor.id)
    after_first = await counts(db_session, competitor.id)

    second = await run_sync(competitor.id)
    after_second = await counts(db_session, competitor.id)

    assert first["new_products"] == 2
    assert second["new_products"] == 0
    assert second["price_changes"] == 0

    assert after_second["products"] == after_first["products"] == 2
    assert after_second["events"] == after_first["events"] == 2
    assert after_second["snapshots"] == after_first["snapshots"] == 2
    assert after_second["runs"] == 2, "each attempt is still recorded as a run"


# ── Failure handling ──────────────────────────────────────────────────────────

async def test_empty_scrape_is_a_failure_and_never_deactivates_products(
    db_session, monkeypatch
):
    """
    Protects the invariant named in docs/PROJECT_STATUS.md §9.2: a transient
    outage must not wipe a catalogue.
    """
    competitor = await make_competitor(db_session)
    product = await make_product(
        db_session, competitor, "Safe", price="4.00",
        url="https://teststore.example/products/safe",
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [])
    result = await run_sync(competitor.id)

    assert result["status"] == "failed"
    assert "0 products" in result["error"]

    db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
    refreshed = (
        await db_session.execute(select(Product).where(Product.id == product.id))
    ).scalar_one()
    assert refreshed.active is True
    assert refreshed.consecutive_misses == 0, "a failed scrape must not count as a miss"

    run = (
        await db_session.execute(
            select(ScrapeRun).where(ScrapeRun.competitor_id == competitor.id)
        )
    ).scalar_one()
    assert run.status == "failed"
    assert "product_removed" not in await event_types(db_session, competitor.id)


async def test_allow_empty_catalog_opt_out(db_session, monkeypatch):
    competitor = await make_competitor(
        db_session, selector_config={"allow_empty_catalog": True}
    )
    await db_session.commit()

    patch_scraper(monkeypatch, [])
    result = await run_sync(competitor.id)

    assert result["status"] == "success"
    assert result["products_found"] == 0


async def test_scraper_exception_records_failure_and_does_not_raise(
    db_session, monkeypatch
):
    competitor = await make_competitor(db_session)
    await db_session.commit()

    patch_scraper(monkeypatch, RuntimeError("connection reset"))
    result = await run_sync(competitor.id)

    assert result["status"] == "failed"
    assert "connection reset" in result["error"]

    run = (
        await db_session.execute(
            select(ScrapeRun).where(ScrapeRun.competitor_id == competitor.id)
        )
    ).scalar_one()
    assert run.status == "failed"
    assert run.finished_at is not None

    db_session.expunge_all()  # drop cached copies; run_sync wrote via its own session
    from app.models import Competitor as C

    competitor_row = (
        await db_session.execute(select(C).where(C.id == competitor.id))
    ).scalar_one()
    assert competitor_row.last_scan_status == "failed"

    assert "scrape_failed" in await event_types(db_session, competitor.id)


async def test_scrape_failed_event_is_never_marked_notified(db_session, monkeypatch):
    """
    BUG (documented): dispatch_event_notifications has no branch for
    scrape_failed, so those events accumulate with notification_sent = false
    forever. Characterising it so the outbox work in Phase 1B fixes it knowingly.
    """
    competitor = await make_competitor(db_session)
    await db_session.commit()

    patch_scraper(monkeypatch, RuntimeError("boom"))
    await run_sync(competitor.id)

    event = (
        await db_session.execute(
            select(Event).where(Event.event_type == "scrape_failed")
        )
    ).scalar_one()
    assert event.notification_sent is False


async def test_inactive_competitor_is_skipped_and_returns_none(db_session, monkeypatch):
    """
    BUG (documented): the early return at tasks.py:42 is a bare `return`, so the
    API surfaces {"message": "Scan completed", "result": null} for a scan that
    never ran. See docs/API_CONTRACTS.md §2.1.
    """
    competitor = await make_competitor(db_session, active=False)
    await db_session.commit()

    patch_scraper(monkeypatch, [observation("X", price=1.0, url="https://teststore.example/x")])
    result = await run_sync(competitor.id)

    assert result is None
    assert (await counts(db_session, competitor.id))["runs"] == 0


# ── Concurrency ───────────────────────────────────────────────────────────────

async def test_concurrent_scans_of_one_competitor_are_not_prevented(
    db_session, monkeypatch
):
    """
    Characterisation of ARCHITECTURE A-6: nothing serialises two overlapping
    scans of the same competitor.

    The scraper is made slow enough that the two runs genuinely overlap. This
    does NOT mock away the race - it demonstrates it.
    """
    competitor = await make_competitor(db_session)
    await db_session.commit()

    async def slow_scrape(competitor_dict, **kwargs):
        await asyncio.sleep(0.3)
        return [observation("Racy", price=5.00, url="https://teststore.example/products/racy")]

    import app.services.scraper as scraper_module

    monkeypatch.setattr(scraper_module, "scrape_competitor", slow_scrape)

    results = await asyncio.gather(
        run_sync(competitor.id), run_sync(competitor.id), return_exceptions=True
    )

    failures = [r for r in results if isinstance(r, Exception)]
    assert not failures, f"concurrent sync raised: {failures}"

    summary = await counts(db_session, competitor.id)

    # Both scans ran: there is no lock and no guard on this path (A-6).
    assert summary["runs"] == 2, (
        "A-6: two overlapping scans both created ScrapeRun rows - no locking exists"
    )

    # CHARACTERISATION OF A DEFECT, NOT A DESIRED PROPERTY.
    #
    # With no advisory lock (A-6) and no UNIQUE (competitor_id, url) (A-7), both
    # scans read an empty product table and both INSERT. One product URL becomes
    # two rows. Downstream, the losing row is never matched again, accrues
    # consecutive_misses, and after three scans emits a FALSE product_removed
    # event - which sales-trends then counts as a phantom signal.
    #
    # Phase 1B is done when this assertion has to be inverted to == 1.
    assert summary["products"] == 2, (
        "Expected the documented duplicate-product race to reproduce. "
        f"Got {summary['products']} product rows. If this is now 1, the race has "
        "been fixed - invert this assertion and update docs/ARCHITECTURE.md A-6/A-7."
    )

    urls = (
        await db_session.execute(
            select(Product.url).where(Product.competitor_id == competitor.id)
        )
    ).scalars().all()
    assert len(set(urls)) == 1, "both rows are the same product URL"


async def test_no_unique_constraint_protects_product_url(db_session):
    """
    Direct proof of A-7: the database accepts two products with the same
    (competitor_id, url). Detection's in-memory {url: product} index is the only
    thing enforcing identity.
    """
    competitor = await make_competitor(db_session)
    url = "https://teststore.example/products/dupe"
    await make_product(db_session, competitor, "Dupe A", price="1.00", url=url)
    await make_product(db_session, competitor, "Dupe B", price="2.00", url=url)
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(Product).where(Product.competitor_id == competitor.id)
        )
    ).scalars().all()

    assert len(rows) == 2, (
        "A-7: PostgreSQL accepted duplicate (competitor_id, url). "
        "Adding UNIQUE requires a data-cleanup migration first."
    )
