from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.database import get_db
from app.models import Competitor
from app.schemas import CompetitorCreate, CompetitorUpdate, CompetitorOut

router = APIRouter(prefix="/api/competitors", tags=["competitors"])


@router.get("", response_model=List[CompetitorOut])
async def list_competitors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Competitor).order_by(Competitor.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=CompetitorOut, status_code=201)
async def create_competitor(data: CompetitorCreate, db: AsyncSession = Depends(get_db)):
    competitor = Competitor(**data.model_dump())
    db.add(competitor)
    await db.flush()
    await db.refresh(competitor)
    return competitor


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
    for field, value in data.model_dump(exclude_none=True).items():
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

    from app.workers.tasks import scrape_competitor_task
    task = scrape_competitor_task.delay(competitor_id)
    return {"message": "Scan queued", "task_id": task.id}
