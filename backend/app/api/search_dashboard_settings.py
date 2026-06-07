from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from typing import Optional
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from app.database import get_db
from app.models import Product, Competitor, Event, ScrapeRun, AppSettings
from app.schemas import ProductOut, EventOut, CompetitorOut, AppSettingsOut, AppSettingsUpdate
from app.utils.text_normalizer import normalize_title

search_router = APIRouter(prefix="/api/search", tags=["search"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])

INFERRED_SALE_EVENT_TYPES = ("stock_out",)
REMOVED_PRODUCT_EVENT_TYPES = ("product_removed",)
SALES_PERIODS = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}
MUTATION_PHRASES = (
    ("yin", "yang"),
    ("blood", "moon"),
    ("bloodrot",),
    ("candy",),
    ("celestial",),
    ("corrupted",),
    ("crystal",),
    ("cursed",),
    ("cyber",),
    ("diamond",),
    ("divine",),
    ("electric",),
    ("galaxy",),
    ("gold",),
    ("golden",),
    ("hacker",),
    ("lava",),
    ("magma",),
    ("radioactive",),
    ("rainbow",),
    ("shadow",),
)
MARKET_STAT_TOKENS = {
    "s", "sec", "second", "seconds", "m", "b", "k", "t", "qn", "qns",
    "best", "game", "in", "read", "description", "plus",
}
BRAINROT_CATEGORY_MARKERS = ("brainrot", "steal a brainrot", "escape tsunami")


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
    query_base_hint = _query_market_base_hint(q)
    rows = await _search_candidate_rows(db, q, 1000)
    grouped = {}
    for product, competitor_name in rows:
        identity = _product_market_identity(product, base_hint=query_base_hint)
        score = max(
            _match_score(normalize_title(q), product.normalized_title),
            _match_score(normalize_title(q), identity["base"]),
        )
        if score < 0.34:
            continue
        key = identity["key"]
        group = grouped.setdefault(key, {
            "title": identity["display_title"],
            "normalized_title": identity["key"],
            "base_title": _title_from_normalized(identity["base"]),
            "base_normalized_title": identity["base"],
            "mutation": identity["mutation"],
            "mutation_label": identity["mutation_label"],
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

    target_identity = _product_market_identity(target_product)
    aliases = _comparison_aliases(target_identity["base"])
    competitors = (await db.execute(select(Competitor).where(Competitor.active == True))).scalars().all()
    rows = (await db.execute(
        select(Product, Competitor.name.label("cname"))
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(Product.active == True)
    )).all()

    best_by_competitor = {}
    for product, competitor_name in rows:
        candidate_identity = _product_market_identity(product, base_hint=target_identity["base"])
        if candidate_identity["mutation"] != target_identity["mutation"]:
            continue
        candidate_aliases = _comparison_aliases(candidate_identity["base"])
        score = max(
            _comparison_score(target_alias, candidate_alias)
            for target_alias in aliases
            for candidate_alias in candidate_aliases
        )
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
        "identity": target_identity,
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


def _product_market_identity(product: Product, base_hint: Optional[str] = None) -> dict:
    return _market_identity(
        product.normalized_title,
        product.category,
        base_hint=base_hint,
    )


def _market_identity(normalized_title: str, category: Optional[str] = None, base_hint: Optional[str] = None) -> dict:
    cleaned = _clean_market_tokens(normalized_title)
    mutation_enabled = _mutation_identity_enabled(normalized_title, category, cleaned, base_hint)
    mutation_tokens = []
    base_tokens = cleaned[:]
    if mutation_enabled:
        mutation_tokens, base_tokens = _extract_mutation_tokens(cleaned)
    base = " ".join(base_tokens).strip() or cleaned and " ".join(cleaned).strip() or normalized_title
    mutation = " ".join(mutation_tokens).strip() or "normal"
    mutation_label = "Normal" if mutation == "normal" else _title_from_normalized(mutation)
    display_title = _title_from_normalized(base) if mutation == "normal" else f"{mutation_label} {_title_from_normalized(base)}"
    return {
        "key": f"{base}::{mutation}",
        "base": base,
        "mutation": mutation,
        "mutation_label": mutation_label,
        "display_title": display_title,
    }


def _query_market_base_hint(query: str) -> str:
    cleaned = _clean_market_tokens(normalize_title(query))
    _, base_tokens = _extract_mutation_tokens(cleaned)
    return " ".join(base_tokens).strip() or " ".join(cleaned).strip()


def _mutation_identity_enabled(
    normalized_title: str,
    category: Optional[str],
    cleaned_tokens: list[str],
    base_hint: Optional[str] = None,
) -> bool:
    text = f"{category or ''} {normalized_title}".lower()
    if any(marker in text for marker in BRAINROT_CATEGORY_MARKERS):
        return True
    tokens = normalized_title.split()
    if any(any(ch.isdigit() for ch in token) and any(unit in token for unit in ("m", "b", "qn")) for token in tokens):
        return True
    if base_hint:
        mutation_tokens, base_tokens = _extract_mutation_tokens(cleaned_tokens)
        if mutation_tokens and " ".join(base_tokens).strip() == base_hint:
            return True
    return False


def _clean_market_tokens(normalized_title: str) -> list[str]:
    tokens = []
    for token in normalized_title.split():
        if any(ch.isdigit() for ch in token):
            continue
        if token in MARKET_STAT_TOKENS:
            continue
        tokens.append(token)
    return tokens


def _extract_mutation_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    mutation_indexes = set()
    mutation_tokens = []
    for phrase in MUTATION_PHRASES:
        length = len(phrase)
        for idx in range(0, len(tokens) - length + 1):
            if tuple(tokens[idx:idx + length]) == phrase:
                mutation_indexes.update(range(idx, idx + length))
                mutation_tokens.extend(phrase)
                break
    base_tokens = [token for idx, token in enumerate(tokens) if idx not in mutation_indexes]
    deduped_mutation = list(dict.fromkeys(mutation_tokens))
    return deduped_mutation, base_tokens


def _title_from_normalized(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split())


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


@dashboard_router.get("/sales-trends")
async def sales_trends(
    period: str = Query("day", pattern="^(day|week|month)$"),
    competitor_id: Optional[int] = None,
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    since = now - SALES_PERIODS[period]
    filters = [
        Event.event_type.in_(INFERRED_SALE_EVENT_TYPES),
        Event.detected_at >= since,
    ]
    removed_filters = [
        Event.event_type.in_(REMOVED_PRODUCT_EVENT_TYPES),
        Event.detected_at >= since,
    ]
    if competitor_id is not None:
        filters.append(Event.competitor_id == competitor_id)
        removed_filters.append(Event.competitor_id == competitor_id)

    competitor_rows = (await db.execute(
        select(
            Competitor.id,
            Competitor.name,
            func.count(Event.id).label("inferred_sold_count"),
            func.count(func.distinct(Event.product_id)).label("unique_products_count"),
            func.max(Event.detected_at).label("last_signal_at"),
        )
        .join(Event, Event.competitor_id == Competitor.id)
        .where(and_(*filters))
        .group_by(Competitor.id, Competitor.name)
        .order_by(func.count(Event.id).desc(), Competitor.name.asc())
    )).all()

    active_competitors = (await db.execute(
        select(Competitor).where(Competitor.active == True).order_by(Competitor.name.asc())
    )).scalars().all()
    removed_rows = (await db.execute(
        select(
            Event.competitor_id,
            func.count(Event.id).label("removed_count"),
        )
        .where(and_(*removed_filters))
        .group_by(Event.competitor_id)
    )).all()
    removed_by_competitor = {
        row.competitor_id: int(row.removed_count or 0)
        for row in removed_rows
    }
    summary_by_competitor = {
        row.id: {
            "competitor_id": row.id,
            "competitor_name": row.name,
            "inferred_sold_count": int(row.inferred_sold_count or 0),
            "removed_count": removed_by_competitor.get(row.id, 0),
            "unique_products_count": int(row.unique_products_count or 0),
            "last_signal_at": row.last_signal_at,
        }
        for row in competitor_rows
    }
    competitors = []
    for competitor in active_competitors:
        if competitor_id is not None and competitor.id != competitor_id:
            continue
        competitors.append(summary_by_competitor.get(competitor.id, {
            "competitor_id": competitor.id,
            "competitor_name": competitor.name,
            "inferred_sold_count": 0,
            "removed_count": removed_by_competitor.get(competitor.id, 0),
            "unique_products_count": 0,
            "last_signal_at": None,
        }))
    competitors.sort(key=lambda item: (-item["inferred_sold_count"], item["competitor_name"]))

    product_rows = (await db.execute(
        select(
            Product.id.label("product_id"),
            Product.title,
            Product.category,
            Product.url,
            Product.image_url,
            Product.current_price,
            Product.currency,
            Product.stock_status,
            Product.competitor_id,
            Competitor.name.label("competitor_name"),
            func.count(Event.id).label("inferred_sold_count"),
            func.max(Event.detected_at).label("last_signal_at"),
        )
        .join(Product, Event.product_id == Product.id)
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(and_(*filters))
        .group_by(
            Product.id,
            Product.title,
            Product.category,
            Product.url,
            Product.image_url,
            Product.current_price,
            Product.currency,
            Product.stock_status,
            Product.competitor_id,
            Competitor.name,
        )
        .order_by(func.count(Event.id).desc(), func.max(Event.detected_at).desc(), Product.title.asc())
        .limit(limit)
    )).all()

    top_products = [
        {
            "product_id": row.product_id,
            "title": row.title,
            "category": row.category,
            "url": row.url,
            "image_url": row.image_url,
            "current_price": float(row.current_price) if row.current_price is not None else None,
            "currency": row.currency,
            "stock_status": row.stock_status,
            "competitor_id": row.competitor_id,
            "competitor_name": row.competitor_name,
            "inferred_sold_count": int(row.inferred_sold_count or 0),
            "last_signal_at": row.last_signal_at,
        }
        for row in product_rows
    ]

    total_signals = sum(item["inferred_sold_count"] for item in competitors)
    total_removed = sum(item["removed_count"] for item in competitors)
    return {
        "period": period,
        "since": since,
        "until": now,
        "signal_types": list(INFERRED_SALE_EVENT_TYPES),
        "removed_signal_types": list(REMOVED_PRODUCT_EVENT_TYPES),
        "total_inferred_sold": total_signals,
        "total_removed": total_removed,
        "competitors": competitors,
        "top_products": top_products,
        "note": "Inferred sales count stock-out events. Removed products are shown separately as a weaker catalog-change signal, not confirmed sales.",
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
