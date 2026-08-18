"""Durable, provider-neutral Sync request, claim, process, and status use cases."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.domain.acquisition import AcquisitionResult, acquire_catalog
from app.models import Competitor, Event, Product, ScrapeRun, SyncRequest, SyncRequestRun


logger = logging.getLogger(__name__)

REQUEST_LOCK_NAMESPACE = 1296912465
PRODUCT_RECONCILIATION_LOCK_NAMESPACE = 1296912466
NON_TERMINAL_STATUSES = ("queued", "running", "retry_wait")


class SyncRequestError(ValueError):
    pass


@dataclass(frozen=True)
class ClaimedRun:
    run_id: int
    claim_token: uuid.UUID
    competitor_id: int
    attempt_count: int


async def _lock_key(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


async def request_competitor_scan(
    session: AsyncSession,
    competitor_id: int,
    *,
    trigger: str = "manual",
    request: SyncRequest | None = None,
    request_idempotency_key: str | None = None,
    run_idempotency_key: str | None = None,
) -> SyncRequest:
    if request is None:
        request = await _get_or_create_request(
            session, trigger=trigger, idempotency_key=request_idempotency_key
        )

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :competitor_id)"),
        {"namespace": REQUEST_LOCK_NAMESPACE, "competitor_id": competitor_id},
    )
    competitor = (
        await session.execute(select(Competitor).where(Competitor.id == competitor_id))
    ).scalar_one_or_none()
    if competitor is None:
        raise SyncRequestError("Competitor not found")
    if not competitor.active:
        raise SyncRequestError("Competitor is inactive")

    run = (
        await session.execute(
            select(ScrapeRun)
            .where(
                ScrapeRun.competitor_id == competitor_id,
                ScrapeRun.status.in_(NON_TERMINAL_STATUSES),
            )
            .order_by(ScrapeRun.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None and run_idempotency_key:
        run = (
            await session.execute(
                select(ScrapeRun).where(
                    ScrapeRun.idempotency_key == run_idempotency_key
                )
            )
        ).scalar_one_or_none()
    if run is None:
        run = ScrapeRun(
            competitor_id=competitor_id,
            status="queued",
            trigger=trigger,
            idempotency_key=run_idempotency_key,
            max_attempts=settings.SYNC_MAX_ATTEMPTS,
            next_attempt_at=datetime.now(timezone.utc),
            completeness="unknown",
        )
        session.add(run)
        await session.flush()

    link = await session.get(
        SyncRequestRun, {"request_id": request.id, "scrape_run_id": run.id}
    )
    if link is None:
        session.add(SyncRequestRun(request_id=request.id, scrape_run_id=run.id))
    await session.flush()
    return request


async def request_all_competitor_scans(
    session: AsyncSession,
    *,
    trigger: str = "manual_all",
    request_idempotency_key: str | None = None,
    local_date: date | None = None,
) -> SyncRequest:
    request = await _get_or_create_request(
        session, trigger=trigger, idempotency_key=request_idempotency_key
    )
    # An existing deterministic morning request already has its complete group.
    existing_links = (
        await session.execute(
            select(SyncRequestRun.scrape_run_id).where(
                SyncRequestRun.request_id == request.id
            )
        )
    ).scalars().all()
    if existing_links:
        return request

    competitor_ids = (
        await session.execute(
            select(Competitor.id).where(Competitor.active.is_(True)).order_by(Competitor.id)
        )
    ).scalars().all()
    for competitor_id in competitor_ids:
        run_key = None
        if trigger == "automatic_morning" and local_date is not None:
            run_key = f"automatic:{local_date.isoformat()}:{competitor_id}"
        await request_competitor_scan(
            session,
            competitor_id,
            trigger=trigger,
            request=request,
            run_idempotency_key=run_key,
        )
    return request


async def _get_or_create_request(
    session: AsyncSession, *, trigger: str, idempotency_key: str | None
) -> SyncRequest:
    if idempotency_key:
        await _lock_key(session, f"sync-request:{idempotency_key}")
        existing = (
            await session.execute(
                select(SyncRequest).where(SyncRequest.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing:
            return existing
    request = SyncRequest(trigger=trigger, idempotency_key=idempotency_key)
    session.add(request)
    await session.flush()
    return request


async def recover_expired_leases(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    runs = (
        await session.execute(
            select(ScrapeRun)
            .where(
                ScrapeRun.status == "running",
                ScrapeRun.lease_expires_at < now,
            )
            .order_by(ScrapeRun.id)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    recovered = abandoned = 0
    for run in runs:
        if run.attempt_count < run.max_attempts:
            run.status = "retry_wait"
            run.next_attempt_at = now
            run.failure_category = "runner_lost"
            run.error_message = "Worker lease expired; queued for recovery"
            recovered += 1
        else:
            run.status = "abandoned"
            run.terminal_at = now
            run.finished_at = now
            run.failure_category = "runner_lost"
            run.error_message = "Worker lease expired after the final attempt"
            session.add(
                Event(
                    competitor_id=run.competitor_id,
                    scrape_run_id=run.id,
                    event_type="scrape_failed",
                    event_message=run.error_message,
                    detected_at=now,
                )
            )
            abandoned += 1
        run.claim_token = None
        run.claimed_by = None
        run.claimed_at = None
        run.lease_expires_at = None
        run.heartbeat_at = None
    return {"recovered": recovered, "abandoned": abandoned}


async def claim_next_run(
    session: AsyncSession,
    *,
    worker_id: str,
    run_id: int | None = None,
    now: datetime | None = None,
) -> ClaimedRun | None:
    now = now or datetime.now(timezone.utc)
    await recover_expired_leases(session, now=now)
    eligible = and_(
        ScrapeRun.status.in_(("queued", "retry_wait")),
        or_(ScrapeRun.next_attempt_at.is_(None), ScrapeRun.next_attempt_at <= now),
    )
    query = select(ScrapeRun).where(eligible)
    if run_id is not None:
        query = query.where(ScrapeRun.id == run_id)
    run = (
        await session.execute(
            query.order_by(ScrapeRun.next_attempt_at, ScrapeRun.queued_at, ScrapeRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if run is None:
        return None

    token = uuid.uuid4()
    run.status = "running"
    run.attempt_count += 1
    run.started_at = run.started_at or now
    run.claimed_at = now
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=settings.SYNC_LEASE_SECONDS)
    run.claim_token = token
    run.claimed_by = worker_id[:255]
    run.next_attempt_at = None
    await session.flush()
    return ClaimedRun(run.id, token, run.competitor_id, run.attempt_count)


async def process_claimed_run(
    claim: ClaimedRun,
    *,
    acquire: Callable[[dict], Awaitable[AcquisitionResult]] | None = None,
) -> dict:
    acquire = acquire or _default_acquire
    competitor_snapshot = await _begin_acquisition(claim)
    if competitor_snapshot is None:
        return {"status": "claim_lost", "run_id": claim.run_id}
    if terminal_status := competitor_snapshot.pop("_terminal_status", None):
        return {"status": terminal_status, "run_id": claim.run_id}
    heartbeat = asyncio.create_task(_heartbeat_lease(claim))
    try:
        result = await acquire(competitor_snapshot)
        return await _reconcile_acquisition(claim, result)
    except Exception as exc:
        return await _record_processing_failure(claim, exc)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def _heartbeat_lease(claim: ClaimedRun) -> None:
    """Renew only the lease identified by the current fencing token."""
    lease_seconds = max(1, settings.SYNC_LEASE_SECONDS)
    interval = max(1.0, min(60.0, lease_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        now = datetime.now(timezone.utc)
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    update(ScrapeRun)
                    .where(
                        ScrapeRun.id == claim.run_id,
                        ScrapeRun.status == "running",
                        ScrapeRun.claim_token == claim.claim_token,
                    )
                    .values(
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=lease_seconds),
                    )
                )
                await session.commit()
                if result.rowcount != 1:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient heartbeat failure is recoverable on the next interval.
            # The claim token still fences reconciliation if the lease expires.
            logger.warning("Sync lease heartbeat failed", extra={"run_id": claim.run_id})


async def _default_acquire(competitor: dict) -> AcquisitionResult:
    return await acquire_catalog(
        competitor,
        max_pages=settings.DEFAULT_MAX_PAGES,
        page_delay=settings.DEFAULT_PAGE_DELAY_SECONDS,
        headless=settings.PLAYWRIGHT_HEADLESS,
        user_agent=settings.USER_AGENT,
    )


async def _begin_acquisition(claim: ClaimedRun) -> dict | None:
    async with AsyncSessionLocal() as session:
        run = (
            await session.execute(
                select(ScrapeRun).where(
                    ScrapeRun.id == claim.run_id,
                    ScrapeRun.status == "running",
                    ScrapeRun.claim_token == claim.claim_token,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            return None
        competitor = await session.get(Competitor, run.competitor_id)
        if competitor is None or not competitor.active:
            await _terminal_without_acquisition(
                session, run, "failed", "invalid_configuration", "Competitor is missing or inactive"
            )
            await session.commit()
            return {"_terminal_status": "failed"}
        now = datetime.now(timezone.utc)
        run.acquisition_started_at = now
        await session.commit()
        return {
            "id": competitor.id,
            "base_url": competitor.base_url,
            "listing_urls": competitor.listing_urls,
            "selector_config": competitor.selector_config,
            "scrape_type": competitor.scrape_type,
        }


async def _reconcile_acquisition(claim: ClaimedRun, result: AcquisitionResult) -> dict:
    from app.services.detection import detect_changes

    async with AsyncSessionLocal() as session:
        run = (
            await session.execute(
                select(ScrapeRun)
                .where(
                    ScrapeRun.id == claim.run_id,
                    ScrapeRun.status == "running",
                    ScrapeRun.claim_token == claim.claim_token,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            return {"status": "claim_lost", "run_id": claim.run_id}
        competitor = await session.get(Competitor, run.competitor_id)
        if competitor is None:
            return {"status": "claim_lost", "run_id": claim.run_id}

        run.acquisition_started_at = result.started_at
        run.acquisition_completed_at = result.completed_at
        run.observation_started_at = result.observation_started_at
        run.observation_completed_at = result.observation_completed_at
        run.products_found = result.product_count
        run.pages_fetched = result.pages_fetched
        run.request_count = result.request_count
        run.page_cap_reached = result.page_cap_reached
        run.acquisition_strategy = result.strategy[:100]
        run.completeness = result.completeness
        run.completeness_reason = (result.completeness_reason or "")[:500] or None

        await session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :competitor_id)"),
            {
                "namespace": PRODUCT_RECONCILIATION_LOCK_NAMESPACE,
                "competitor_id": competitor.id,
            },
        )
        later_complete_id = (
            await session.execute(
                select(ScrapeRun.id)
                .where(
                    ScrapeRun.competitor_id == competitor.id,
                    ScrapeRun.status == "success",
                    ScrapeRun.completeness == "complete",
                    or_(
                        ScrapeRun.observation_completed_at > result.observation_completed_at,
                        and_(
                            ScrapeRun.observation_completed_at == result.observation_completed_at,
                            ScrapeRun.id > run.id,
                        ),
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        stale = later_complete_id is not None
        if stale:
            changes = {"new_products": 0, "price_changes": 0}
        else:
            changes = await detect_changes(
                session,
                competitor,
                result.observations,
                scrape_run_id=run.id,
                observed_at=result.observation_completed_at,
                allow_absence=result.completeness == "complete",
            )

        now = datetime.now(timezone.utc)
        run.status = "stale_skipped" if stale else "success"
        run.reconciled_at = now
        run.terminal_at = now
        run.finished_at = now
        run.new_products_count = changes["new_products"]
        run.price_changes_count = changes["price_changes"]
        run.failure_category = None
        run.error_message = None
        run.claim_token = None
        run.lease_expires_at = None
        run.heartbeat_at = now
        if not stale:
            competitor.last_scan_at = result.completed_at
            competitor.last_scan_status = (
                "success" if result.completeness == "complete" else result.completeness
            )
        await session.commit()
        return {
            "status": run.status,
            "run_id": run.id,
            "completeness": run.completeness,
            "products_found": run.products_found,
            "new_products": run.new_products_count,
            "price_changes": run.price_changes_count,
        }


async def _record_processing_failure(claim: ClaimedRun, exc: Exception) -> dict:
    category, message, retryable = _safe_failure(exc)
    async with AsyncSessionLocal() as session:
        run = (
            await session.execute(
                select(ScrapeRun)
                .where(
                    ScrapeRun.id == claim.run_id,
                    ScrapeRun.status == "running",
                    ScrapeRun.claim_token == claim.claim_token,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            return {"status": "claim_lost", "run_id": claim.run_id}
        now = datetime.now(timezone.utc)
        run.completeness = "failed"
        run.failure_category = category
        run.error_message = message
        run.claim_token = None
        run.claimed_by = None
        run.claimed_at = None
        run.lease_expires_at = None
        run.heartbeat_at = None
        if retryable and run.attempt_count < run.max_attempts:
            run.status = "retry_wait"
            run.next_attempt_at = now + timedelta(seconds=60 * (2 ** (run.attempt_count - 1)))
        else:
            run.status = "failed"
            run.finished_at = now
            run.terminal_at = now
            competitor = await session.get(Competitor, run.competitor_id)
            if competitor:
                competitor.last_scan_at = now
                competitor.last_scan_status = "failed"
            session.add(
                Event(
                    competitor_id=run.competitor_id,
                    scrape_run_id=run.id,
                    event_type="scrape_failed",
                    event_message=message,
                    detected_at=now,
                )
            )
        await session.commit()
        return {
            "status": run.status,
            "run_id": run.id,
            "failure_category": category,
            "attempt": run.attempt_count,
        }


async def _terminal_without_acquisition(
    session: AsyncSession, run: ScrapeRun, status: str, category: str, message: str
) -> None:
    now = datetime.now(timezone.utc)
    run.status = status
    run.completeness = "failed"
    run.failure_category = category
    run.error_message = message
    run.finished_at = now
    run.terminal_at = now
    run.claim_token = None
    run.lease_expires_at = None


def _safe_failure(exc: Exception) -> tuple[str, str, bool]:
    if all(hasattr(exc, field) for field in ("category", "safe_message", "retryable")):
        return str(exc.category), str(exc.safe_message), bool(exc.retryable)
    name = type(exc).__name__.lower()
    value = str(exc).lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name:
        return "timeout", "External catalog request timed out", True
    if any(marker in value for marker in ("429", "rate limit")):
        return "rate_limited", "Storefront rate limit requested a retry", True
    if any(marker in value for marker in ("502", "503", "504", "connection reset", "temporar")):
        return "temporary_network", "Temporary storefront/network failure", True
    if any(marker in value for marker in ("unsupported", "no selector", "invalid config")):
        return "invalid_configuration", "Competitor scraping configuration is invalid", False
    return "acquisition_error", "Catalog acquisition failed", True


async def get_request_status(session: AsyncSession, request_id: uuid.UUID) -> dict | None:
    request = (
        await session.execute(
            select(SyncRequest)
            .where(SyncRequest.id == request_id)
            .options(selectinload(SyncRequest.runs).selectinload(ScrapeRun.competitor))
        )
    ).scalar_one_or_none()
    if request is None:
        return None
    now = datetime.now(timezone.utc)
    runs = [_run_status(run, now=now) for run in request.runs]
    queued_ages = [run["queue_age_seconds"] for run in runs if run["status"] == "queued"]
    oldest_queued_seconds = max(queued_ages, default=None)
    if any(run["status"] == "running" for run in runs):
        runner_state = "running"
    elif any(run["status"] == "retry_wait" for run in runs):
        runner_state = "retry_wait"
    elif not any(run["status"] == "queued" for run in runs):
        runner_state = "terminal"
    elif request.dispatch_status == "failed":
        runner_state = "dispatch_recovery"
    elif oldest_queued_seconds is not None and oldest_queued_seconds >= settings.SYNC_RUNNER_WAIT_SECONDS:
        runner_state = "waiting_for_runner"
    elif request.dispatch_status == "dispatched":
        runner_state = "dispatched"
    else:
        runner_state = "awaiting_dispatch"
    return {
        "request_id": request.id,
        "trigger": request.trigger,
        "status": _aggregate_status(request.runs),
        "requested_at": request.requested_at,
        "dispatch_status": request.dispatch_status,
        "dispatch_error_category": request.dispatch_error_category,
        "runner_state": runner_state,
        "needs_runner_recovery": runner_state in {"dispatch_recovery", "waiting_for_runner"},
        "oldest_queued_seconds": oldest_queued_seconds,
        "runs": runs,
    }


async def get_run_status(session: AsyncSession, run_id: int) -> dict | None:
    run = (
        await session.execute(
            select(ScrapeRun)
            .where(ScrapeRun.id == run_id)
            .options(selectinload(ScrapeRun.competitor))
        )
    ).scalar_one_or_none()
    return _run_status(run) if run else None


async def get_competitor_freshness(session: AsyncSession) -> list[dict]:
    competitors = (
        await session.execute(select(Competitor).order_by(Competitor.name))
    ).scalars().all()
    rows = []
    for competitor in competitors:
        runs = (
            await session.execute(
                select(ScrapeRun)
                .where(ScrapeRun.competitor_id == competitor.id)
                .order_by(ScrapeRun.queued_at.desc(), ScrapeRun.id.desc())
            )
        ).scalars().all()
        complete_runs = [
            r for r in runs if r.status == "success" and r.completeness == "complete"
        ]
        partial_runs = [
            r for r in runs
            if r.status == "success" and r.completeness in ("partial", "suspicious_empty")
        ]
        failed_runs = [r for r in runs if r.status in ("failed", "abandoned")]
        complete = max(complete_runs, key=_observation_order, default=None)
        partial = max(partial_runs, key=_observation_order, default=None)
        failed = max(
            failed_runs, key=lambda r: (r.terminal_at or r.queued_at, r.id), default=None
        )
        active = next((r for r in runs if r.status in NON_TERMINAL_STATUSES), None)
        active_products = await session.scalar(
            select(func.count(Product.id)).where(
                Product.competitor_id == competitor.id,
                Product.active.is_(True),
            )
        )
        rows.append(
            {
                "competitor_id": competitor.id,
                "competitor_name": competitor.name,
                "coverage_complete": complete is not None and (
                    partial is None or _observation_order(complete) >= _observation_order(partial)
                ),
                "last_complete_at": complete.observation_completed_at if complete else None,
                "latest_partial_at": partial.observation_completed_at if partial else None,
                "last_failed_at": failed.terminal_at if failed else None,
                "active_products": active_products or 0,
                "last_complete_run": _run_status(complete) if complete else None,
                "active_run": _run_status(active) if active else None,
            }
        )
    return rows


def _run_status(run: ScrapeRun, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    duration = None
    if run.started_at and run.finished_at:
        duration = max(0.0, (run.finished_at - run.started_at).total_seconds())
    queue_latency = max(0.0, (run.claimed_at - run.queued_at).total_seconds()) if run.claimed_at else None
    acquisition_duration = (
        max(0.0, (run.acquisition_completed_at - run.acquisition_started_at).total_seconds())
        if run.acquisition_started_at and run.acquisition_completed_at else None
    )
    reconciliation_duration = (
        max(0.0, (run.reconciled_at - run.acquisition_completed_at).total_seconds())
        if run.reconciled_at and run.acquisition_completed_at else None
    )
    queue_age_seconds = max(0.0, (now - run.queued_at).total_seconds())
    if run.status == "queued" and queue_age_seconds >= settings.SYNC_RUNNER_WAIT_SECONDS:
        operator_state = "waiting_for_runner"
    elif run.status == "retry_wait":
        operator_state = "retry_scheduled"
    elif run.status == "running" and run.lease_expires_at and run.lease_expires_at < now:
        operator_state = "lease_expired"
    else:
        operator_state = run.status
    return {
        "run_id": run.id,
        "competitor_id": run.competitor_id,
        "competitor_name": run.competitor.name if run.competitor else None,
        "status": run.status,
        "trigger": run.trigger,
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "claimed_at": run.claimed_at,
        "acquisition_started_at": run.acquisition_started_at,
        "acquisition_completed_at": run.acquisition_completed_at,
        "observation_started_at": run.observation_started_at,
        "observation_completed_at": run.observation_completed_at,
        "reconciled_at": run.reconciled_at,
        "terminal_at": run.terminal_at,
        "attempt": run.attempt_count,
        "max_attempts": run.max_attempts,
        "next_attempt_at": run.next_attempt_at,
        "lease_expires_at": run.lease_expires_at,
        "failure_category": run.failure_category,
        "failure_reason": run.error_message,
        "products_observed": run.products_found,
        "pages_fetched": run.pages_fetched,
        "request_count": run.request_count,
        "page_cap_reached": run.page_cap_reached,
        "acquisition_strategy": run.acquisition_strategy,
        "completeness": run.completeness,
        "completeness_reason": run.completeness_reason,
        "duration_seconds": duration,
        "queue_latency_seconds": queue_latency,
        "acquisition_duration_seconds": acquisition_duration,
        "reconciliation_duration_seconds": reconciliation_duration,
        "queue_age_seconds": queue_age_seconds,
        "operator_state": operator_state,
    }


def _aggregate_status(runs: list[ScrapeRun]) -> str:
    if not runs:
        return "success"
    statuses = {run.status for run in runs}
    if "running" in statuses:
        return "running"
    if "retry_wait" in statuses:
        return "retrying"
    if "queued" in statuses:
        return "queued"
    if statuses <= {"failed", "abandoned"}:
        return "failed"
    if any(
        run.completeness in ("partial", "suspicious_empty") or run.status in ("failed", "abandoned")
        for run in runs
    ):
        return "partial"
    return "success"


def _observation_order(run: ScrapeRun) -> tuple[datetime, int]:
    return (run.observation_completed_at or run.queued_at, run.id)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
