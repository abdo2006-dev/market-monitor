import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
from app.database import get_db
from app.models import Competitor, ScrapeRun
from app.schemas import CompetitorCreate, CompetitorUpdate, CompetitorOut
from app.config import settings
from app.services.default_competitors import default_competitor_payloads

router = APIRouter(prefix="/api/competitors", tags=["competitors"])

SHOPIFY_SELECTOR_CONFIG = {"discover_collections": True, "include_all_products": True}
SCAN_ALL_INLINE_CONCURRENCY = 4


@router.get("", response_model=List[CompetitorOut])
async def list_competitors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor).order_by(Competitor.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=CompetitorOut, status_code=201)
async def create_competitor(data: CompetitorCreate, db: AsyncSession = Depends(get_db)):
    competitor = Competitor(**_normalize_competitor_payload(data.model_dump()))
    db.add(competitor)
    await db.flush()
    await db.refresh(competitor)
    return competitor


@router.post("/seed-defaults", response_model=List[CompetitorOut])
async def seed_default_competitors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor.base_url))
    existing_urls = {row[0].rstrip("/") for row in result.all()}
    created = []
    for payload in default_competitor_payloads():
        if payload["base_url"].rstrip("/") in existing_urls:
            continue
        competitor = Competitor(**payload)
        db.add(competitor)
        created.append(competitor)
    await db.flush()
    for competitor in created:
        await db.refresh(competitor)
    return created


@router.post("/scan-all")
async def scan_all(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor).where(Competitor.active == True).order_by(Competitor.name))
    competitors = result.scalars().all()
    if not competitors:
        return {"message": "No active competitors to scan", "total": 0, "queued": 0, "completed": 0, "skipped": 0, "items": []}

    runnable = []
    items = []
    for competitor in competitors:
        if await _has_recent_running_scan(db, competitor.id):
            items.append({
                "competitor_id": competitor.id,
                "name": competitor.name,
                "status": "skipped",
                "reason": "Scan already running",
            })
            continue
        runnable.append(competitor)

    queued = []
    inline = []
    if settings.RUN_SCANS_INLINE:
        inline = runnable
    else:
        for competitor in runnable:
            if competitor.scrape_type in {"shopify_json", "salla_json"}:
                inline.append(competitor)
            else:
                queued.append(competitor)

    for competitor in queued:
        from app.workers.tasks import scrape_competitor_task
        task = scrape_competitor_task.delay(competitor.id)
        items.append({
            "competitor_id": competitor.id,
            "name": competitor.name,
            "status": "queued",
            "task_id": task.id,
        })

    completed = []
    if inline:
        from app.workers.tasks import _scrape_competitor_async
        semaphore = asyncio.Semaphore(SCAN_ALL_INLINE_CONCURRENCY)

        async def run_scan(competitor: Competitor):
            async with semaphore:
                scan_result = await _scrape_competitor_async(competitor.id)
                return {
                    "competitor_id": competitor.id,
                    "name": competitor.name,
                    "status": scan_result.get("status") if scan_result else "skipped",
                    "result": scan_result,
                }

        completed = await asyncio.gather(*(run_scan(competitor) for competitor in inline))
        items.extend(completed)

    return {
        "message": "Scan all completed" if inline and not queued else "Scan all started",
        "total": len(competitors),
        "queued": len(queued),
        "completed": len(completed),
        "skipped": sum(1 for item in items if item["status"] == "skipped"),
        "items": items,
    }


@router.get("/{competitor_id}", response_model=CompetitorOut)
async def get_competitor(competitor_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor).where(Competitor.id == competitor_id))
    competitor = result.scalar_one_or_none()
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")
    return competitor


@router.put("/{competitor_id}", response_model=CompetitorOut)
async def update_competitor(competitor_id: int, data: CompetitorUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor).where(Competitor.id == competitor_id))
    competitor = result.scalar_one_or_none()
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")
    for field, value in _normalize_competitor_payload(data.model_dump(exclude_none=True)).items():
        setattr(competitor, field, value)
    await db.flush()
    await db.refresh(competitor)
    return competitor


@router.delete("/{competitor_id}", status_code=204)
async def delete_competitor(competitor_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor).where(Competitor.id == competitor_id))
    competitor = result.scalar_one_or_none()
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")
    await db.delete(competitor)


@router.post("/{competitor_id}/scan-now")
async def scan_now(competitor_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor).where(Competitor.id == competitor_id))
    competitor = result.scalar_one_or_none()
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")
    if not competitor.active:
        raise HTTPException(status_code=400, detail="Competitor is inactive")

    if settings.RUN_SCANS_INLINE or competitor.scrape_type in {"shopify_json", "salla_json"}:
        from app.workers.tasks import _scrape_competitor_async
        result = await _scrape_competitor_async(competitor_id)
        return {"message": "Scan completed", "result": result}

    from app.workers.tasks import scrape_competitor_task
    task = scrape_competitor_task.delay(competitor_id)
    return {"message": "Scan queued", "task_id": task.id}


async def _has_recent_running_scan(db: AsyncSession, competitor_id: int) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    running_result = await db.execute(
        select(ScrapeRun).where(
            and_(
                ScrapeRun.competitor_id == competitor_id,
                ScrapeRun.status == "running",
                ScrapeRun.started_at > cutoff,
            )
        )
    )
    return running_result.scalar_one_or_none() is not None


def _normalize_competitor_payload(payload: dict) -> dict:
    listing_urls = payload.get("listing_urls") or []
    scrape_type = payload.get("scrape_type")

    selector_config = payload.get("selector_config") or {}
    if scrape_type != "salla_json" and _looks_like_salla_catalog(payload.get("base_url"), listing_urls, selector_config):
        payload["scrape_type"] = "salla_json"
        payload["selector_config"] = selector_config
    elif not scrape_type or (scrape_type in {"generic_selector", "custom"} and not listing_urls):
        payload["scrape_type"] = "shopify_json"
        payload["listing_urls"] = []
        payload["selector_config"] = _shopify_selector_config(payload.get("selector_config"))
    elif scrape_type == "shopify_json":
        payload["selector_config"] = _shopify_selector_config(payload.get("selector_config"))

    return payload


def _shopify_selector_config(selector_config: dict | None) -> dict:
    return {**SHOPIFY_SELECTOR_CONFIG, **(selector_config or {})}


def _looks_like_salla_catalog(base_url: str | None, listing_urls: list[str], selector_config: dict) -> bool:
    if selector_config.get("platform") == "salla" or selector_config.get("category_id") or selector_config.get("category_ids"):
        return True
    urls = [base_url or "", *listing_urls]
    return any("/api/v1/products" in url or "/c" in url and any(ch.isdigit() for ch in url.rsplit("/c", 1)[-1]) for url in urls)
