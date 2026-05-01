from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.database import get_db
from app.models import Competitor
from app.schemas import CompetitorCreate, CompetitorUpdate, CompetitorOut
from app.config import settings
from app.services.default_competitors import default_competitor_payloads

router = APIRouter(prefix="/api/competitors", tags=["competitors"])

SHOPIFY_SELECTOR_CONFIG = {"discover_collections": True, "include_all_products": True}


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

    if settings.RUN_SCANS_INLINE or competitor.scrape_type == "shopify_json":
        from app.workers.tasks import _scrape_competitor_async
        result = await _scrape_competitor_async(competitor_id)
        return {"message": "Scan completed", "result": result}

    from app.workers.tasks import scrape_competitor_task
    task = scrape_competitor_task.delay(competitor_id)
    return {"message": "Scan queued", "task_id": task.id}


def _normalize_competitor_payload(payload: dict) -> dict:
    listing_urls = payload.get("listing_urls") or []
    scrape_type = payload.get("scrape_type")

    if not scrape_type or (scrape_type in {"generic_selector", "custom"} and not listing_urls):
        payload["scrape_type"] = "shopify_json"
        payload["listing_urls"] = []
        payload["selector_config"] = _shopify_selector_config(payload.get("selector_config"))
    elif scrape_type == "shopify_json":
        payload["selector_config"] = _shopify_selector_config(payload.get("selector_config"))

    return payload


def _shopify_selector_config(selector_config: dict | None) -> dict:
    return {**SHOPIFY_SELECTOR_CONFIG, **(selector_config or {})}
