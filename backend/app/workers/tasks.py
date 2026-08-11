import asyncio
import logging
from datetime import datetime, timezone, timedelta
from celery import shared_task
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Two-key PostgreSQL advisory-lock namespace: "MMPR" + competitor id.
PRODUCT_RECONCILIATION_LOCK_NAMESPACE = 1296912466
PRODUCT_IDENTITY_CONSTRAINTS = {
    "uq_products_competitor_canonical_url",
    "uq_products_competitor_identity_key",
}


def run_async(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def scrape_competitor_task(self, competitor_id: int):
    """Scrape a single competitor and process results."""
    return run_async(_scrape_competitor_async(competitor_id))


async def _scrape_competitor_async(competitor_id: int):
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import AsyncSessionLocal
    from app.models import Competitor, ScrapeRun, Event, Product
    from app.services.scraper import scrape_competitor
    from app.services.detection import detect_changes
    from app.services.notification import dispatch_event_notifications
    from app.config import settings
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Competitor).where(Competitor.id == competitor_id))
        competitor = result.scalar_one_or_none()
        if not competitor or not competitor.active:
            logger.warning(f"Competitor {competitor_id} not found or inactive")
            return

        # Create scrape run record
        scrape_run = ScrapeRun(
            competitor_id=competitor_id,
            started_at=now,
            status="running",
        )
        session.add(scrape_run)
        await session.commit()
        scrape_run_id = scrape_run.id

        try:
            competitor_dict = {
                "id": competitor.id,
                "base_url": competitor.base_url,
                "listing_urls": competitor.listing_urls or [],
                "selector_config": competitor.selector_config or {},
                "scrape_type": competitor.scrape_type,
            }

            products = await scrape_competitor(
                competitor_dict,
                max_pages=settings.DEFAULT_MAX_PAGES,
                page_delay=settings.DEFAULT_PAGE_DELAY_SECONDS,
                headless=settings.PLAYWRIGHT_HEADLESS,
                user_agent=settings.USER_AGENT,
            )
            if _should_reject_empty_scrape(competitor_dict, products):
                raise RuntimeError(
                    "Scrape returned 0 products. Treating this as a failed scan to avoid marking the catalog missing."
                )

            try:
                changes, is_initial_scan, stale_observation = await _reconcile_products(
                    session,
                    competitor,
                    scrape_run,
                    products,
                    detect_changes,
                )
            except IntegrityError as exc:
                constraint = _integrity_constraint_name(exc)
                if constraint not in PRODUCT_IDENTITY_CONSTRAINTS:
                    raise
                await session.rollback()
                logger.warning(
                    "Product identity race resolved by retry for competitor_id=%s "
                    "scrape_run_id=%s constraint=%s",
                    competitor_id,
                    scrape_run_id,
                    constraint,
                    extra={
                        "competitor_id": competitor_id,
                        "scrape_run_id": scrape_run_id,
                        "operation": "reconcile_products",
                        "product_identity": _observation_identity_context(products),
                        "constraint": constraint,
                    },
                )
                competitor = (
                    await session.execute(
                        select(Competitor).where(Competitor.id == competitor_id)
                    )
                ).scalar_one()
                scrape_run = (
                    await session.execute(
                        select(ScrapeRun).where(ScrapeRun.id == scrape_run_id)
                    )
                ).scalar_one()
                changes, is_initial_scan, stale_observation = await _reconcile_products(
                    session,
                    competitor,
                    scrape_run,
                    products,
                    detect_changes,
                )

            scrape_run.status = "success"
            scrape_run.finished_at = datetime.now(timezone.utc)
            scrape_run.products_found = len(products)
            scrape_run.new_products_count = changes["new_products"]
            scrape_run.price_changes_count = changes["price_changes"]

            if not stale_observation:
                competitor.last_scan_at = datetime.now(timezone.utc)
                competitor.last_scan_status = "success"

            await session.commit()

            # Send notifications
            events_result = await session.execute(
                select(Event).where(
                    Event.competitor_id == competitor_id,
                    Event.notification_sent == False,
                )
            )
            pending_events = events_result.scalars().all()
            if pending_events:
                product_ids = [e.product_id for e in pending_events if e.product_id]
                products_result = await session.execute(
                    select(Product).where(Product.id.in_(product_ids))
                )
                products_map = {p.id: p for p in products_result.scalars().all()}
                if not product_ids:
                    products_map = {}
                competitors_map = {competitor.id: competitor}
                new_product_backlog = sum(1 for event in pending_events if event.event_type == "new_product")
                if is_initial_scan or new_product_backlog > 25:
                    for event in pending_events:
                        if event.event_type == "new_product":
                            event.notification_sent = True
                            event.notification_sent_at = datetime.now(timezone.utc)
                    notify_events = [event for event in pending_events if event.event_type != "new_product"]
                else:
                    notify_events = pending_events

                await dispatch_event_notifications(
                    session, notify_events, competitors_map, products_map,
                    settings.DISCORD_NOTIFICATIONS_ENABLED,
                    settings.DISCORD_DEFAULT_WEBHOOK_URL,
                )
                await session.commit()

            return {
                "status": "success",
                "products_found": len(products),
                "new_products": changes["new_products"],
                "price_changes": changes["price_changes"],
            }

        except Exception as e:
            await session.rollback()
            logger.error(f"Scrape failed for competitor {competitor_id}: {e}")
            competitor = (
                await session.execute(
                    select(Competitor).where(Competitor.id == competitor_id)
                )
            ).scalar_one()
            scrape_run = (
                await session.execute(
                    select(ScrapeRun).where(ScrapeRun.id == scrape_run_id)
                )
            ).scalar_one()
            scrape_run.status = "failed"
            scrape_run.finished_at = datetime.now(timezone.utc)
            scrape_run.error_message = str(e)[:1000]
            competitor.last_scan_at = datetime.now(timezone.utc)
            competitor.last_scan_status = "failed"

            # Create failure event
            fail_event = Event(
                competitor_id=competitor_id,
                event_type="scrape_failed",
                event_message=str(e)[:500],
                detected_at=datetime.now(timezone.utc),
            )
            session.add(fail_event)
            await session.commit()

            failure_webhook_url = competitor.discord_webhook_url or settings.DISCORD_DEFAULT_WEBHOOK_URL
            if failure_webhook_url:
                from app.services.notification import notify_scrape_failure
                await notify_scrape_failure(failure_webhook_url, competitor.name, str(e))
            return {"status": "failed", "error": str(e)}


async def _reconcile_products(
    session,
    competitor,
    scrape_run,
    products: list[dict],
    detect_changes,
) -> tuple[dict, bool, bool]:
    """Serialize the short database reconciliation region for one competitor."""
    from app.models import Product, ScrapeRun
    from sqlalchemy import and_, or_, select, text

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :competitor_id)"),
        {
            "namespace": PRODUCT_RECONCILIATION_LOCK_NAMESPACE,
            "competitor_id": competitor.id,
        },
    )
    # ScrapeRun.started_at is captured before acquisition and persisted in the
    # initial run-record transaction.  Under the advisory lock, a committed
    # later-started successful run is the durable ordering watermark. Failed
    # runs do not participate and therefore cannot erase a prior success.
    later_success_id = (
        await session.execute(
            select(ScrapeRun.id)
            .where(
                ScrapeRun.competitor_id == competitor.id,
                ScrapeRun.status == "success",
                or_(
                    ScrapeRun.started_at > scrape_run.started_at,
                    and_(
                        ScrapeRun.started_at == scrape_run.started_at,
                        ScrapeRun.id > scrape_run.id,
                    ),
                ),
            )
            .order_by(ScrapeRun.started_at.desc(), ScrapeRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if later_success_id is not None:
        logger.warning(
            "Skipped stale reconciliation for competitor_id=%s scrape_run_id=%s; "
            "later_successful_scrape_run_id=%s already committed",
            competitor.id,
            scrape_run.id,
            later_success_id,
            extra={
                "competitor_id": competitor.id,
                "scrape_run_id": scrape_run.id,
                "later_successful_scrape_run_id": later_success_id,
                "operation": "skip_stale_reconciliation",
            },
        )
        return {"new_products": 0, "price_changes": 0}, False, True

    existing = (
        await session.execute(
            select(Product.id).where(Product.competitor_id == competitor.id).limit(1)
        )
    ).scalar_one_or_none()
    changes = await detect_changes(session, competitor, products)
    return changes, existing is None, False


def _integrity_constraint_name(exc) -> str | None:
    current = getattr(exc, "orig", None)
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        constraint = getattr(current, "constraint_name", None)
        if constraint:
            return constraint
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None


def _observation_identity_context(products: list[dict]) -> list[dict]:
    from app.domain.product_identity import canonicalize_product_url, product_identity_key

    context = []
    for item in products[:5]:
        try:
            canonical_url = canonicalize_product_url(item.get("url", ""))
        except ValueError:
            canonical_url = "invalid"
        context.append(
            {
                "canonical_url": canonical_url,
                "identity_key": product_identity_key(item.get("external_id")),
            }
        )
    return context


def _should_reject_empty_scrape(competitor: dict, products: list[dict]) -> bool:
    selector_config = competitor.get("selector_config") or {}
    return not products and selector_config.get("allow_empty_catalog") is not True


@celery_app.task
def check_and_schedule_scans():
    """Check which competitors need scanning and queue tasks."""
    return run_async(_check_and_schedule_async())


async def _check_and_schedule_async():
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import AsyncSessionLocal
    from app.models import Competitor, ScrapeRun
    from sqlalchemy import select, and_

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Competitor).where(Competitor.active == True)
        )
        competitors = result.scalars().all()

        now = datetime.now(timezone.utc)
        for competitor in competitors:
            freq = competitor.scan_frequency_minutes or 60
            cutoff = now - timedelta(minutes=freq)

            last_scan = competitor.last_scan_at
            if last_scan is None or last_scan < cutoff:
                # Check no running scan
                running_result = await session.execute(
                    select(ScrapeRun).where(
                        and_(
                            ScrapeRun.competitor_id == competitor.id,
                            ScrapeRun.status == "running",
                            ScrapeRun.started_at > now - timedelta(minutes=30),
                        )
                    )
                )
                running = running_result.scalar_one_or_none()
                if not running:
                    logger.info(f"Scheduling scan for competitor {competitor.id}: {competitor.name}")
                    scrape_competitor_task.delay(competitor.id)


@celery_app.task
def send_daily_summary_task():
    """Send daily summary to all configured Discord webhooks."""
    return run_async(_send_daily_summary_async())


async def _send_daily_summary_async():
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import AsyncSessionLocal
    from app.models import Competitor, Event, ScrapeRun
    from app.services.notification import send_daily_summary
    from app.config import settings
    from sqlalchemy import select, and_, func
    from datetime import date

    async with AsyncSessionLocal() as session:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        new_products = await session.execute(
            select(func.count(Event.id)).where(
                and_(Event.event_type == "new_product", Event.detected_at >= today_start)
            )
        )
        price_changes = await session.execute(
            select(func.count(Event.id)).where(
                and_(Event.event_type.in_(["price_increase", "price_decrease"]), Event.detected_at >= today_start)
            )
        )
        failed_scans = await session.execute(
            select(func.count(ScrapeRun.id)).where(
                and_(ScrapeRun.status == "failed", ScrapeRun.started_at >= today_start)
            )
        )
        competitors_scanned = await session.execute(
            select(func.count(func.distinct(ScrapeRun.competitor_id))).where(
                ScrapeRun.started_at >= today_start
            )
        )

        summary = {
            "new_products_today": new_products.scalar() or 0,
            "price_changes_today": price_changes.scalar() or 0,
            "failed_scans": failed_scans.scalar() or 0,
            "competitors_scanned": competitors_scanned.scalar() or 0,
            "biggest_drops": [],
        }

        # Get all unique Discord webhooks, including the global fallback.
        comps_result = await session.execute(select(Competitor).where(Competitor.active == True))
        competitors = comps_result.scalars().all()
        seen_webhooks = set()
        if settings.DISCORD_DEFAULT_WEBHOOK_URL:
            seen_webhooks.add(settings.DISCORD_DEFAULT_WEBHOOK_URL)
        for comp in competitors:
            if comp.discord_webhook_url and comp.discord_webhook_url not in seen_webhooks:
                seen_webhooks.add(comp.discord_webhook_url)
        if settings.DISCORD_NOTIFICATIONS_ENABLED:
            for webhook_url in seen_webhooks:
                await send_daily_summary(webhook_url, summary)
