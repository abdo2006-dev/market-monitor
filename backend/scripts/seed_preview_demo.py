#!/usr/bin/env python3
"""Seed deterministic, non-sensitive data into the isolated V2 preview database.

The script deliberately reads ``PREVIEW_DATABASE_URL`` rather than
``DATABASE_URL`` and requires two explicit preview gates. It deletes and
replaces only rows carrying the preview marker in ``Competitor.notes``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal


PREVIEW_MARKER = "MARKET_MONITOR_V2_PREVIEW_DEMO_V1"
REQUIRED_CONFIRMATION = "market-monitor-v2-preview-db"


def _guard_preview_target() -> str:
    database_url = os.environ.get("PREVIEW_DATABASE_URL", "")
    if os.environ.get("VERCEL_ENV") != "preview":
        raise SystemExit("Refusing to seed: VERCEL_ENV must be preview")
    if os.environ.get("PREVIEW_DEMO_MODE", "").lower() != "true":
        raise SystemExit("Refusing to seed: PREVIEW_DEMO_MODE must be true")
    if os.environ.get("PREVIEW_SEED_CONFIRM") != REQUIRED_CONFIRMATION:
        raise SystemExit("Refusing to seed: preview confirmation is missing")
    if not database_url:
        raise SystemExit("Refusing to seed: PREVIEW_DATABASE_URL is missing")
    if "localhost" in database_url or "127.0.0.1" in database_url:
        raise SystemExit("Refusing to seed: use the isolated remote preview database")
    return database_url


os.environ["DATABASE_URL"] = _guard_preview_target()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.domain.product_identity import (  # noqa: E402
    canonicalize_product_url,
    product_identity_key,
)
from app.models import (  # noqa: E402
    Competitor,
    Event,
    Product,
    ProductSnapshot,
    ScrapeRun,
)
from app.utils.text_normalizer import normalize_title  # noqa: E402


async def _make_run(session, competitor: Competitor, *, status: str, completeness: str,
                    minutes_ago: int, reason: str, products_found: int = 3) -> ScrapeRun:
    observed_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    terminal = status in {"success", "failed", "abandoned", "stale_skipped"}
    run = ScrapeRun(
        competitor_id=competitor.id,
        queued_at=observed_at - timedelta(minutes=2),
        started_at=observed_at - timedelta(minutes=1) if status != "queued" else None,
        finished_at=observed_at if terminal else None,
        terminal_at=observed_at if terminal else None,
        status=status,
        trigger="preview_demo",
        idempotency_key=f"preview:{competitor.name}:{status}:{completeness}",
        acquisition_started_at=observed_at - timedelta(seconds=45) if status not in {"queued", "retry_wait"} else None,
        acquisition_completed_at=observed_at - timedelta(seconds=5) if terminal else None,
        reconciled_at=observed_at if status == "success" else None,
        attempt_count=2 if status == "retry_wait" else (1 if status != "queued" else 0),
        max_attempts=3,
        next_attempt_at=(observed_at + timedelta(minutes=15)) if status == "retry_wait" else None,
        claimed_at=(observed_at - timedelta(minutes=1)) if status == "running" else None,
        lease_expires_at=(observed_at + timedelta(minutes=20)) if status == "running" else None,
        heartbeat_at=observed_at if status == "running" else None,
        claim_token=uuid.uuid4() if status == "running" else None,
        claimed_by="preview-fixture-runner" if status == "running" else None,
        products_found=products_found if status not in {"queued", "retry_wait", "running", "failed"} else 0,
        pages_fetched=2 if status == "success" else 0,
        request_count=2 if status == "success" else 0,
        page_cap_reached=completeness == "partial",
        acquisition_strategy=f"preview_fixture_{completeness}",
        completeness=completeness,
        completeness_reason=reason,
        observation_started_at=(observed_at - timedelta(seconds=40)) if status == "success" else None,
        observation_completed_at=observed_at if status == "success" else None,
        failure_category="preview_fixture_failure" if status in {"failed", "retry_wait"} else None,
        error_message=(
            "Preview fixture demonstrates a safe acquisition failure."
            if status == "failed"
            else "Preview fixture is waiting for a retry; no external request will run."
            if status == "retry_wait"
            else None
        ),
        new_products_count=0,
        price_changes_count=1 if status == "success" and completeness == "complete" else 0,
    )
    session.add(run)
    await session.flush()
    return run


async def _make_product(session, competitor: Competitor, run: ScrapeRun | None, *,
                        title: str, price: Decimal, suffix: str,
                        stock_status: str = "in_stock", legacy: bool = False) -> Product:
    observed_at = None if legacy else (
        run.observation_completed_at if run and run.observation_completed_at
        else datetime.now(timezone.utc) - timedelta(days=3)
    )
    checked_at = observed_at or datetime.now(timezone.utc) - timedelta(days=5)
    url = f"{competitor.base_url.rstrip('/')}/products/{suffix}"
    external_id = f"preview-{suffix}"
    product = Product(
        competitor_id=competitor.id,
        external_id=external_id,
        identity_key=product_identity_key(external_id),
        title=title,
        normalized_title=normalize_title(title),
        category="MM2 Knives",
        url=url,
        canonical_url=canonicalize_product_url(url, competitor.scrape_type),
        image_url=None,
        current_price=price,
        currency="USD",
        stock_status=stock_status,
        sku=f"PREVIEW-{suffix.upper()}",
        first_seen_at=checked_at - timedelta(days=7),
        last_seen_at=checked_at,
        last_checked_at=checked_at,
        last_observed_at=observed_at,
        last_observed_run_id=run.id if run and observed_at else None,
        active=True,
        consecutive_misses=0,
    )
    session.add(product)
    await session.flush()

    if title == "Batwing":
        previous_price = price + Decimal("15.00")
        session.add(ProductSnapshot(
            product_id=product.id,
            title=title,
            category="MM2 Knives",
            price=previous_price,
            currency="USD",
            stock_status=stock_status,
            checked_at=checked_at - timedelta(days=2),
            observed_at=(observed_at - timedelta(days=2)) if observed_at else None,
            scrape_run_id=None,
        ))
    session.add(ProductSnapshot(
        product_id=product.id,
        title=title,
        category="MM2 Knives",
        price=price,
        currency="USD",
        stock_status=stock_status,
        checked_at=checked_at,
        observed_at=observed_at,
        scrape_run_id=run.id if run and observed_at else None,
    ))
    return product


async def seed() -> None:
    now = datetime.now(timezone.utc)
    specs = [
        ("Preview Alpha — complete + queued", "alpha.preview.invalid", "complete", Decimal("84.99"), "queued"),
        ("Preview Beta — partial + retrying", "beta.preview.invalid", "partial", Decimal("74.50"), "retry_wait"),
        ("Preview Gamma — failed", "gamma.preview.invalid", "failed", Decimal("82.00"), None),
        ("Preview Delta — suspicious empty", "delta.preview.invalid", "suspicious_empty", Decimal("78.00"), None),
        ("Preview Epsilon — legacy observation", "epsilon.preview.invalid", "legacy", Decimal("69.00"), None),
        ("Preview Omega — complete + running", "omega.preview.invalid", "complete", Decimal("60.00"), "running"),
        ("Preview Zeta — current complete", "zeta.preview.invalid", "complete", Decimal("89.00"), None),
    ]

    async with AsyncSessionLocal() as session:
        existing_ids = (
            await session.execute(select(Competitor.id).where(Competitor.notes == PREVIEW_MARKER))
        ).scalars().all()
        if existing_ids:
            await session.execute(delete(Competitor).where(Competitor.id.in_(existing_ids)))
            await session.flush()

        for index, (name, host, state, price, active_state) in enumerate(specs):
            base_url = f"https://{host}"
            selector = {
                "preview_demo": True,
                "preview_export_state": "complete" if state == "legacy" else state,
                "preview_collection_url": f"{base_url}/collections/mm2-knives",
                "preview_fixture_price": float(price),
                "preview_fixture_stock_status": "out_of_stock" if "Omega" in name else "in_stock",
            }
            competitor = Competitor(
                name=name,
                base_url=base_url,
                category="MM2",
                active=True,
                scan_frequency_minutes=60,
                scrape_type="shopify_json",
                listing_urls=[selector["preview_collection_url"]],
                selector_config=selector,
                discord_webhook_url=None,
                notes=PREVIEW_MARKER,
                last_scan_at=now - timedelta(minutes=5 + index),
                last_scan_status=(
                    None if state == "legacy" else "success" if state == "complete" else state
                ),
            )
            session.add(competitor)
            await session.flush()

            product_run: ScrapeRun | None = None
            if state == "complete":
                product_run = await _make_run(
                    session, competitor, status="success", completeness="complete",
                    minutes_ago=8 + index,
                    reason="Preview fixture reached a deterministic catalog end signal.",
                )
            elif state == "partial":
                await _make_run(
                    session, competitor, status="success", completeness="complete",
                    minutes_ago=60 * 48,
                    reason="Older complete preview coverage.",
                )
                product_run = await _make_run(
                    session, competitor, status="success", completeness="partial",
                    minutes_ago=18,
                    reason="Preview fixture hit its page cap; absence inference is disabled.",
                    products_found=2,
                )
            elif state == "failed":
                product_run = await _make_run(
                    session, competitor, status="success", completeness="complete",
                    minutes_ago=60 * 72,
                    reason="Older complete preview coverage.",
                )
                await _make_run(
                    session, competitor, status="failed", completeness="failed",
                    minutes_ago=12,
                    reason="Preview fixture demonstrates a failed acquisition.",
                    products_found=0,
                )
            elif state == "suspicious_empty":
                product_run = await _make_run(
                    session, competitor, status="success", completeness="complete",
                    minutes_ago=60 * 48,
                    reason="Older complete preview coverage.",
                )
                await _make_run(
                    session, competitor, status="success", completeness="suspicious_empty",
                    minutes_ago=14,
                    reason="Unexpected zero-product preview result; absence inference is disabled.",
                    products_found=0,
                )

            legacy = state == "legacy"
            batwing = await _make_product(
                session, competitor, product_run, title="Batwing", price=price,
                suffix="batwing",
                stock_status="out_of_stock" if "Omega" in name else "in_stock",
                legacy=legacy,
            )
            await _make_product(
                session, competitor, product_run, title="Elderwood Scythe",
                price=price + Decimal("18.50"), suffix="elderwood-scythe", legacy=legacy,
            )
            await _make_product(
                session, competitor, product_run, title="Harvester",
                price=price + Decimal("31.00"), suffix="harvester",
                stock_status="out_of_stock", legacy=legacy,
            )

            if name.startswith("Preview Alpha"):
                session.add(Event(
                    competitor_id=competitor.id,
                    product_id=batwing.id,
                    event_type="price_decrease",
                    old_value={"price": float(price + Decimal("15.00")), "currency": "USD"},
                    new_value={"price": float(price), "currency": "USD"},
                    event_message="Preview Batwing price decreased by $15.00.",
                    detected_at=now - timedelta(minutes=8),
                    notification_sent=True,
                    notification_sent_at=now - timedelta(minutes=8),
                    scrape_run_id=product_run.id if product_run else None,
                ))

            if active_state:
                await _make_run(
                    session, competitor,
                    status=active_state,
                    completeness="unknown",
                    minutes_ago=2 + index,
                    reason=(
                        "Preview-only durable request; no external runner is connected."
                        if active_state == "queued"
                        else "Preview-only lifecycle example; it will not advance automatically."
                    ),
                    products_found=0,
                )

        await session.commit()

    print("Seeded 7 preview competitors, 21 products, and deterministic lifecycle evidence.")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except Exception as exc:
        print(f"Preview seed failed: {type(exc).__name__}", file=sys.stderr)
        raise
