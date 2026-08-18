"""HTTP transport for the durable Sync V2 lifecycle."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.sync import (
    SyncRequestError,
    get_competitor_freshness,
    get_request_status,
    get_run_status,
    request_all_competitor_scans,
    request_competitor_scan,
)
from app.database import get_db
from app.infrastructure.github_actions import dispatch_sync_request
from app.schemas import CompetitorFreshness, SyncRequestStatus, SyncRunStatus


router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post(
    "/competitors/{competitor_id}",
    response_model=SyncRequestStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_single(
    competitor_id: int,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=180  # gitleaks:allow -- header name
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        request = await request_competitor_scan(
            db,
            competitor_id,
            trigger="manual",
            request_idempotency_key=(f"manual:{idempotency_key}" if idempotency_key else None),
        )
    except SyncRequestError as exc:
        code = 404 if str(exc) == "Competitor not found" else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    request_id = request.id
    await db.commit()
    await dispatch_sync_request(request_id)
    db.expire_all()
    payload = await get_request_status(db, request_id)
    if payload is None:
        raise HTTPException(status_code=500, detail="Durable Sync request could not be reloaded")
    response.headers["Location"] = f"/api/sync/requests/{request_id}"
    return payload


@router.post(
    "/all", response_model=SyncRequestStatus, status_code=status.HTTP_202_ACCEPTED
)
async def request_all(
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=180  # gitleaks:allow -- header name
    ),
    db: AsyncSession = Depends(get_db),
):
    request = await request_all_competitor_scans(
        db,
        trigger="manual_all",
        request_idempotency_key=(f"manual-all:{idempotency_key}" if idempotency_key else None),
    )
    request_id = request.id
    await db.commit()
    await dispatch_sync_request(request_id)
    db.expire_all()
    payload = await get_request_status(db, request_id)
    if payload is None:
        raise HTTPException(status_code=500, detail="Durable Sync request could not be reloaded")
    response.headers["Location"] = f"/api/sync/requests/{request_id}"
    return payload


@router.get("/requests/{request_id}", response_model=SyncRequestStatus)
async def request_status(request_id: UUID, db: AsyncSession = Depends(get_db)):
    payload = await get_request_status(db, request_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Sync request not found")
    return payload


@router.post(
    "/requests/{request_id}/dispatch",
    response_model=SyncRequestStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_request_dispatch(request_id: UUID, db: AsyncSession = Depends(get_db)):
    """Retry runner notification for the same durable request; never create new work."""
    payload = await get_request_status(db, request_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Sync request not found")
    if payload["status"] not in {"queued", "retrying"}:
        raise HTTPException(status_code=409, detail="Sync request is not waiting for recovery")
    await dispatch_sync_request(request_id)
    db.expire_all()
    refreshed = await get_request_status(db, request_id)
    if refreshed is None:
        raise HTTPException(status_code=500, detail="Durable Sync request could not be reloaded")
    return refreshed


@router.get("/runs/{run_id}", response_model=SyncRunStatus)
async def run_status(run_id: int, db: AsyncSession = Depends(get_db)):
    payload = await get_run_status(db, run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Sync run not found")
    return payload


@router.get("/freshness", response_model=list[CompetitorFreshness])
async def freshness(db: AsyncSession = Depends(get_db)):
    return await get_competitor_freshness(db)
