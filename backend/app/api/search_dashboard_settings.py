from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from typing import Optional
from datetime import datetime, timezone
from difflib import SequenceMatcher
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

    # Keep the database query broad, then do fuzzy ranking in Python so spelling can be imperfect.
    if tokens:
        fuzzy_tokens = {t for token in tokens for t in _fuzzy_token_variants(token)}
        token_filters = [Product.normalized_title.ilike(f"%{t}%") for t in fuzzy_tokens if len(t) >= 2]
        if token_filters:
            query = query.where(or_(*token_filters))

    query = query.order_by(Product.last_checked_at.desc()).limit(1000)
    result = await db.execute(query)
    rows = result.all()

    items = []
    for row in rows:
        product, cname = row
        score = _match_score(norm, product.normalized_title)
        if score < 0.34:
            continue
        d = ProductOut.model_validate(product)
        d.competitor_name = cname
        item = d.model_dump()
        item["match_score"] = round(score, 3)
        items.append(item)

    items.sort(key=lambda item: (-item["match_score"], item["current_price"] is None, item["current_price"] or 0))
    total = len(items)
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": total, "page": page, "page_size": page_size, "query": q}


@search_router.get("/suggestions")
async def search_suggestions(
    q: str = Query(..., min_length=1),
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    rows = await _search_candidate_rows(db, q, 1000)
    grouped = {}
    for product, competitor_name in rows:
        score = _match_score(normalize_title(q), product.normalized_title)
        if score < 0.34:
            continue
        key = product.normalized_title
        group = grouped.setdefault(key, {
            "title": product.title,
            "normalized_title": product.normalized_title,
            "category": product.category,
            "representative_product_id": product.id,
            "best_price": product.current_price,
            "currency": product.currency,
            "image_url": product.image_url,
            "competitors": set(),
            "variants": set(),
            "match_score": score,
        })
        group["competitors"].add(competitor_name)
        group["variants"].add(product.title)
        group["match_score"] = max(group["match_score"], score)
        if product.current_price is not None and (group["best_price"] is None or product.current_price < group["best_price"]):
            group["best_price"] = product.current_price
            group["currency"] = product.currency
            group["representative_product_id"] = product.id
            group["image_url"] = product.image_url

    items = []
    for group in grouped.values():
        item = dict(group)
        item["competitors_count"] = len(group["competitors"])
        item["competitors"] = sorted(group["competitors"])
        item["variants"] = sorted(group["variants"])[:5]
        item["match_score"] = round(group["match_score"], 3)
        items.append(item)

    items.sort(key=lambda item: (-item["match_score"], -item["competitors_count"], item["best_price"] is None, item["best_price"] or 0))
    return {"items": items[:limit], "total": len(items), "query": q}


@search_router.get("/compare")
async def compare_product(
    q: Optional[str] = None,
    product_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    target_product = None
    if product_id is not None:
        result = await db.execute(select(Product).where(Product.id == product_id))
        target_product = result.scalar_one_or_none()
    if not target_product and q:
        rows = await _search_candidate_rows(db, q, 1000)
        ranked = sorted(
            ((product, _comparison_score(normalize_title(q), product.normalized_title)) for product, _ in rows),
            key=lambda item: item[1],
            reverse=True,
        )
        target_product = ranked[0][0] if ranked else None
    if not target_product:
        return {"target": None, "items": [], "total_matches": 0}

    target_norm = target_product.normalized_title
    aliases = _comparison_aliases(target_norm)
    competitors = (await db.execute(select(Competitor).where(Competitor.active == True))).scalars().all()
    rows = (await db.execute(
        select(Product, Competitor.name.label("cname"))
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(Product.active == True)
    )).all()

    best_by_competitor = {}
    for product, competitor_name in rows:
        score = max(_comparison_score(alias, product.normalized_title) for alias in aliases)
        if score < 0.86:
            continue
        current = best_by_competitor.get(product.competitor_id)
        if not current or score > current["match_score"] or (
            score == current["match_score"] and product.current_price is not None and (
                current["product"]["current_price"] is None or product.current_price < current["product"]["current_price"]
            )
        ):
            item = ProductOut.model_validate(product).model_dump()
            item["competitor_name"] = competitor_name
            best_by_competitor[product.competitor_id] = {
                "competitor_id": product.competitor_id,
                "competitor_name": competitor_name,
                "match_score": round(score, 3),
                "product": item,
            }

    items = []
    for competitor in competitors:
        match = best_by_competitor.get(competitor.id)
        items.append(match or {
            "competitor_id": competitor.id,
            "competitor_name": competitor.name,
            "match_score": 0,
            "product": None,
        })

    items.sort(key=lambda item: (
        item["product"] is None,
        item["product"]["current_price"] is None if item["product"] else True,
        item["product"]["current_price"] if item["product"] else 0,
    ))
    target = ProductOut.model_validate(target_product).model_dump()
    return {
        "target": target,
        "aliases": sorted(aliases),
        "items": items,
        "total_matches": sum(1 for item in items if item["product"]),
    }


async def _search_candidate_rows(db: AsyncSession, q: str, limit: int):
    norm = normalize_title(q)
    tokens = norm.split()
    query = (
        select(Product, Competitor.name.label("cname"))
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(Product.active == True)
    )
    if tokens:
        fuzzy_tokens = {t for token in tokens for t in _fuzzy_token_variants(token)}
        token_filters = [Product.normalized_title.ilike(f"%{t}%") for t in fuzzy_tokens if len(t) >= 2]
        if token_filters:
            query = query.where(or_(*token_filters))
    query = query.order_by(Product.last_checked_at.desc()).limit(limit)
    return (await db.execute(query)).all()


def _fuzzy_token_variants(token: str) -> list[str]:
    variants = {token}
    if len(token) > 4:
        variants.add(token[:4])
    if len(token) > 6:
        variants.add(token[:6])
    return list(variants)


def _match_score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0
    q_tokens = set(query.split())
    c_tokens = set(candidate.split())
    overlap = len(q_tokens & c_tokens) / max(len(q_tokens), 1)
    substring = 1.0 if query in candidate else 0.0
    sequence = SequenceMatcher(None, query, candidate).ratio()
    token_similarity = max(
        (SequenceMatcher(None, qt, ct).ratio() for qt in q_tokens for ct in c_tokens),
        default=0.0,
    )
    return max(substring, overlap * 0.9, sequence * 0.8, token_similarity * 0.7)


def _comparison_aliases(normalized_title: str) -> set[str]:
    tokens = normalized_title.split()
    descriptor_tokens = {"knife", "knive", "knives", "gun", "guns", "weapon", "weapons", "godly", "mm2"}
    compact = " ".join(t for t in tokens if t not in descriptor_tokens)
    aliases = {normalized_title}
    if compact:
        aliases.add(compact)
    return aliases


def _comparison_score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0
    if query == candidate:
        return 1.0
    q_tokens = query.split()
    c_tokens = candidate.split()
    if len(q_tokens) == 1 and q_tokens[0] in c_tokens:
        return 0.94 if len(c_tokens) == 1 else 0.72
    if set(q_tokens).issubset(set(c_tokens)):
        return 0.9
    return SequenceMatcher(None, query, candidate).ratio()


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
        select(Event, Competitor.name.label("cname"), Product.title.label("ptitle"), Product.category.label("pcategory"))
        .join(Competitor, Event.competitor_id == Competitor.id)
        .outerjoin(Product, Event.product_id == Product.id)
        .order_by(Event.detected_at.desc())
        .limit(20)
    )
    latest_events = []
    for row in events_result.all():
        event, cname, ptitle, pcategory = row
        d = EventOut.model_validate(event)
        d.competitor_name = cname
        d.product_title = ptitle
        d.product_category = pcategory
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
