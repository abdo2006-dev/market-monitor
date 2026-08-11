"""Provider-neutral PostgreSQL Sync worker CLI.

The same module is used by GitHub Actions, Railway, and local development.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.application.sync import (
    claim_next_run,
    default_worker_id,
    process_claimed_run,
    recover_expired_leases,
    request_all_competitor_scans,
)
from app.database import AsyncSessionLocal
from app.models import ScrapeRun, SyncRequestRun


def _emit(event: str, **fields) -> None:
    safe = {"event": event, **fields}
    print(json.dumps(safe, default=str, sort_keys=True), flush=True)


async def _claim(worker_id: str, run_id: int | None = None):
    async with AsyncSessionLocal() as session:
        claim = await claim_next_run(session, worker_id=worker_id, run_id=run_id)
        await session.commit()
        return claim


async def _recover() -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        result = await recover_expired_leases(session)
        await session.commit()
        return result


async def _request_run_ids(request_id: UUID) -> list[int]:
    async with AsyncSessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(SyncRequestRun.scrape_run_id)
                    .where(SyncRequestRun.request_id == request_id)
                    .order_by(SyncRequestRun.scrape_run_id)
                )
            ).scalars().all()
        )


async def _exit_code_for_runs(run_ids: list[int]) -> int:
    async with AsyncSessionLocal() as session:
        states = set(
            (
                await session.execute(
                    select(ScrapeRun.status).where(ScrapeRun.id.in_(run_ids))
                )
            ).scalars().all()
        )
    if states & {"failed", "abandoned"}:
        return 1
    if "retry_wait" in states:
        return 2
    if states & {"queued", "running"}:
        return 3
    return 0


async def _ensure_morning_request() -> UUID:
    cairo_date = datetime.now(ZoneInfo("Africa/Cairo")).date()
    key = f"automatic:{cairo_date.isoformat()}"
    async with AsyncSessionLocal() as session:
        request = await request_all_competitor_scans(
            session,
            trigger="automatic_morning",
            request_idempotency_key=key,
            local_date=cairo_date,
        )
        await session.commit()
        _emit("morning_request_ready", request_id=request.id, local_date=cairo_date)
        return request.id


async def _execute(worker_id: str, run_id: int | None = None) -> tuple[bool, dict | None]:
    claim = await _claim(worker_id, run_id)
    if claim is None:
        return False, None
    _emit(
        "run_claimed",
        run_id=claim.run_id,
        competitor_id=claim.competitor_id,
        attempt=claim.attempt_count,
    )
    result = await process_claimed_run(claim)
    _emit("run_finished", **result)
    return True, result


async def _main(args: argparse.Namespace) -> int:
    worker_id = args.worker_id or default_worker_id()
    recovery = await _recover()
    _emit("lease_recovery", **recovery)

    try:
        request_id = UUID(args.request_id) if args.request_id else None
    except ValueError:
        _emit("invalid_request_id")
        return 3
    morning_request_id = None
    if args.morning:
        morning_request_id = await _ensure_morning_request()
        # Recovery opportunities drain every eligible durable job, including a
        # manual request whose optional API dispatch previously failed.
        request_id = None

    if args.run_id is not None:
        claimed, result = await _execute(worker_id, args.run_id)
        if not claimed:
            _emit("no_claimable_run", run_id=args.run_id)
            return 3
        if result and result.get("status") in {"failed", "abandoned", "claim_lost"}:
            return 1
        return 2 if result and result.get("status") == "retry_wait" else 0

    if request_id is not None:
        run_ids = await _request_run_ids(request_id)
        if not run_ids:
            _emit("request_has_no_runs", request_id=request_id)
            return 3
        for run_id in run_ids:
            await _execute(worker_id, run_id)
        return await _exit_code_for_runs(run_ids)

    if args.once:
        claimed, result = await _execute(worker_id)
        if not claimed or result is None:
            return 0
        if result.get("status") in {"failed", "abandoned", "claim_lost"}:
            return 1
        return 2 if result.get("status") == "retry_wait" else 0

    # Drain only work that is eligible now. retry_wait rows with a future
    # next_attempt_at are intentionally left for the next ephemeral invocation.
    processed_run_ids = []
    while True:
        claimed, result = await _execute(worker_id)
        if not claimed:
            break
        if result and result.get("run_id") is not None:
            processed_run_ids.append(int(result["run_id"]))
    _emit("drain_complete")
    if morning_request_id is not None:
        return await _exit_code_for_runs(await _request_run_ids(morning_request_id))
    return await _exit_code_for_runs(processed_run_ids)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drain durable Market Monitor Sync jobs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run-id", type=int)
    mode.add_argument("--request-id")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--drain", action="store_true")
    mode.add_argument("--morning", action="store_true")
    parser.add_argument("--worker-id", help="Non-secret diagnostic worker label")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not any((args.run_id, args.request_id, args.once, args.drain, args.morning)):
        args.once = True
    try:
        return asyncio.run(_main(args))
    except Exception as exc:
        _emit("worker_failed", failure_category=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
