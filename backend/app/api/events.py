from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import Event, Competitor, Product
from app.schemas import EventOut

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
async def list_events(
    event_type: Optional[str] = None,
    competitor_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    notification_sent: Optional[bool] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Event, Competitor.name.label("cname"), Product.title.label("ptitle"))
        .join(Competitor, Event.competitor_id == Competitor.id)
        .outerjoin(Product, Event.product_id == Product.id)
    )

    filters = []
    if event_type:
        filters.append(Event.event_type == event_type)
    if competitor_id:
        filters.append(Event.competitor_id == competitor_id)
    if date_from:
        filters.append(Event.detected_at >= date_from)
    if date_to:
        filters.append(Event.detected_at <= date_to)
    if notification_sent is not None:
        filters.append(Event.notification_sent == notification_sent)

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(Event.detected_at.desc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    items = []
    for row in rows:
        event, cname, ptitle = row
        d = EventOut.model_validate(event)
        d.competitor_name = cname
        d.product_title = ptitle
        items.append(d.model_dump())

    return {"items": items, "total": total, "page": page, "page_size": page_size}
