from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional
from datetime import datetime, timezone
from app.database import get_db
from app.models import Product, Competitor, Event, ScrapeRun, AppSettings
from app.schemas import ProductOut, EventOut, CompetitorOut, AppSettingsOut, AppSettingsUpdate
from app.utils.text_normalizer import normalize_title

search_router = APIRouter(prefix="/api/search", tags=["search"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── Search ────────────────────────────────────────────────────────────────────

@search_router.get("/products")
async def search_products(
    q: str = Query(..., min_length=1),
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    norm = normalize_title(q)
    tokens = norm.split()

    query = (
        select(Product, Competitor.name.label("cname"))
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(Product.active == True)
    )

    # Filter by any token appearing in normalized_title
    if tokens:
        token_filters = [Product.normalized_title.ilike(f"%{t}%") for t in tokens]
        from sqlalchemy import or_
        query = query.where(or_(*token_filters))

    query = query.order_by(Product.current_price.asc().nullslast())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    items = []
    for row in rows:
        product, cname = row
        d = ProductOut.model_validate(product)
        d.competitor_name = cname
        items.append(d.model_dump())

    return {"items": items, "total": total, "page": page, "page_size": page_size, "query": q}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@dashboard_router.get("/summary")
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    new_products_today = (await db.execute(
        select(func.count(Event.id)).where(
            and_(Event.event_type == "new_product", Event.detected_at >= today)
        )
    )).scalar() or 0

    price_changes_today = (await db.execute(
        select(func.count(Event.id)).where(
            and_(Event.event_type.in_(["price_increase", "price_decrease", "price_changed"]),
                 Event.detected_at >= today)
        )
    )).scalar() or 0

    price_drops_today = (await db.execute(
        select(func.count(Event.id)).where(
            and_(Event.event_type == "price_decrease", Event.detected_at >= today)
        )
    )).scalar() or 0

    price_increases_today = (await db.execute(
        select(func.count(Event.id)).where(
            and_(Event.event_type == "price_increase", Event.detected_at >= today)
        )
    )).scalar() or 0

    failed_scans_today = (await db.execute(
        select(func.count(ScrapeRun.id)).where(
            and_(ScrapeRun.status == "failed", ScrapeRun.started_at >= today)
        )
    )).scalar() or 0

    # Latest events
    events_result = await db.execute(
        select(Event, Competitor.name.label("cname"), Product.title.label("ptitle"))
        .join(Competitor, Event.competitor_id == Competitor.id)
        .outerjoin(Product, Event.product_id == Product.id)
        .order_by(Event.detected_at.desc())
        .limit(20)
    )
    latest_events = []
    for row in events_result.all():
        event, cname, ptitle = row
        d = EventOut.model_validate(event)
        d.competitor_name = cname
        d.product_title = ptitle
        latest_events.append(d.model_dump())

    # Competitors needing attention (failed scan or no scan in 2x interval)
    comps_result = await db.execute(
        select(Competitor).where(Competitor.active == True)
    )
    competitors_needing = []
    for c in comps_result.scalars().all():
        if c.last_scan_status == "failed":
            competitors_needing.append(CompetitorOut.model_validate(c).model_dump())

    return {
        "new_products_today": new_products_today,
        "price_changes_today": price_changes_today,
        "price_drops_today": price_drops_today,
        "price_increases_today": price_increases_today,
        "failed_scans_today": failed_scans_today,
        "latest_events": latest_events,
        "competitors_needing_attention": competitors_needing,
    }


# ── Settings ─────────────────────────────────────────────────────────────────

@settings_router.get("", response_model=AppSettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = AppSettings(id=1)
        db.add(settings)
        await db.flush()
        await db.refresh(settings)
    return settings


@settings_router.put("", response_model=AppSettingsOut)
async def update_settings(data: AppSettingsUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = AppSettings(id=1)
        db.add(settings)
        await db.flush()

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(settings, field, value)
    await db.flush()
    await db.refresh(settings)
    return settings
