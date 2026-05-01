from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Competitor, ScrapeRun
from app.workers.tasks import _scrape_competitor_async, _send_daily_summary_async


router = APIRouter(prefix="/api/cron", tags=["cron"])


def _check_auth(authorization: str | None):
    if settings.CRON_SECRET and authorization != f"Bearer {settings.CRON_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/scan-due")
async def scan_due(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    _check_auth(authorization)
    result = await db.execute(select(Competitor).where(Competitor.active == True))
    competitors = result.scalars().all()
    now = datetime.now(timezone.utc)
    scanned = []

    for competitor in competitors:
        freq = competitor.scan_frequency_minutes or 60
        cutoff = now - timedelta(minutes=freq)
        if competitor.last_scan_at is not None and competitor.last_scan_at >= cutoff:
            continue

        running_result = await db.execute(
            select(ScrapeRun).where(
                and_(
                    ScrapeRun.competitor_id == competitor.id,
                    ScrapeRun.status == "running",
                    ScrapeRun.started_at > now - timedelta(minutes=30),
                )
            )
        )
        if running_result.scalar_one_or_none():
            continue

        scan_result = await _scrape_competitor_async(competitor.id)
        scanned.append({"competitor_id": competitor.id, "name": competitor.name, "result": scan_result})

    return {"scanned": scanned, "count": len(scanned)}


@router.get("/daily-summary")
async def daily_summary(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    result = await _send_daily_summary_async()
    return {"status": "ok", "result": result}


@router.get("/daily")
async def daily(authorization: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    _check_auth(authorization)
    scan_result = await scan_due(authorization=authorization, db=db)
    summary_result = await _send_daily_summary_async()
    return {"status": "ok", "scan": scan_result, "summary": summary_result}
