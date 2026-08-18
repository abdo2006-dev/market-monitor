"""Optional server-only GitHub Actions dispatch adapter for durable Sync."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import uuid

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import SyncRequest


logger = logging.getLogger(__name__)


async def dispatch_sync_request(request_id: uuid.UUID) -> str:
    if settings.SYNC_DISPATCH_PROVIDER != "github_actions":
        logger.info("Sync dispatch not requested", extra={"sync_request_id": str(request_id)})
        return "not_requested"
    if not settings.GITHUB_ACTIONS_DISPATCH_TOKEN or not settings.GITHUB_ACTIONS_REPOSITORY:
        await _record_dispatch(request_id, "failed", "dispatcher_not_configured")
        logger.warning(
            "Sync dispatch needs recovery",
            extra={"sync_request_id": str(request_id), "failure_category": "dispatcher_not_configured"},
        )
        return "failed"

    url = (
        "https://api.github.com/repos/"
        f"{settings.GITHUB_ACTIONS_REPOSITORY}/actions/workflows/"
        f"{settings.GITHUB_ACTIONS_WORKFLOW}/dispatches"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {settings.GITHUB_ACTIONS_DISPATCH_TOKEN}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "ref": settings.GITHUB_ACTIONS_REF,
                    "inputs": {"request_id": str(request_id)},
                },
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        category = f"github_http_{exc.response.status_code}"
        await _record_dispatch(request_id, "failed", category)
        logger.warning("Sync dispatch needs recovery", extra={"sync_request_id": str(request_id), "failure_category": category})
        return "failed"
    except httpx.RequestError:
        await _record_dispatch(request_id, "failed", "github_unreachable")
        logger.warning("Sync dispatch needs recovery", extra={"sync_request_id": str(request_id), "failure_category": "github_unreachable"})
        return "failed"
    except ValueError:
        await _record_dispatch(request_id, "failed", "dispatcher_configuration")
        logger.warning("Sync dispatch needs recovery", extra={"sync_request_id": str(request_id), "failure_category": "dispatcher_configuration"})
        return "failed"

    await _record_dispatch(request_id, "dispatched", None)
    logger.info("Sync runner dispatched", extra={"sync_request_id": str(request_id)})
    return "dispatched"


async def _record_dispatch(
    request_id: uuid.UUID, status: str, error_category: str | None
) -> None:
    async with AsyncSessionLocal() as session:
        request = (
            await session.execute(
                select(SyncRequest).where(SyncRequest.id == request_id).with_for_update()
            )
        ).scalar_one_or_none()
        if request is None:
            return
        request.dispatch_status = status
        request.dispatch_error_category = error_category
        request.dispatched_at = datetime.now(timezone.utc) if status == "dispatched" else None
        await session.commit()
