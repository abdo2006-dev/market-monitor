from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from typing import Optional
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from app.database import get_db
from app.domain.search_trust import (
    CompetitorEvidence,
    RunEvidence,
    assess_search_trust,
)
from app.models import Product, ProductSnapshot, Competitor, Event, ScrapeRun, AppSettings
from app.schemas import (
    ProductOut,
    EventOut,
    CompetitorOut,
    AppSettingsOut,
    AppSettingsUpdate,
    SearchCompareResponse,
    SearchSuggestionsResponse,
)
from app.utils.text_normalizer import normalize_title

search_router = APIRouter(prefix="/api/search", tags=["search"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


class BatchCompareRequest(BaseModel):
    queries: list[str] = Field(..., min_length=1, max_length=100)
    include_unmatched: bool = True


MAX_BATCH_COMPARE_QUERIES = 100
SEARCH_CANDIDATE_LIMIT = 1000

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
GENERIC_COLLECTION_TOKENS = {
    "", "uncategorized", "roblox", "roblox marketplace", "marketplace",
    "best selling weapons and pets", "best-selling weapons and pets", "bloxy store",
    "knife", "knives", "gun", "guns", "weapon", "weapons", "bundle", "bundles",
    "bloxloot", "bloxshop", "blox shop", "bloxybarn", "bloxy barn", "buyblox",
    "luger gg", "mm2cheap", "petpatch gg", "shopbloxs", "shopify", "zyron",
}
COLLECTION_ALIASES = {
    "adopt me": ("adopt me", "adoptme", "adm"),
    "grow a garden": ("grow a garden", "grow garden", "garden", "gag"),
    "grow a garden 2": ("grow a garden 2", "grow garden 2", "gag2", "gag 2"),
    "steal a brainrot": ("steal a brainrot", "brainrot", "sab"),
    "murder mystery 2": ("murder mystery 2", "mm2"),
    "blox fruits": ("blox fruits", "blox fruit", "bf"),
    "pet simulator 99": ("pet simulator 99", "ps99", "pet sim 99"),
    "sailor piece": ("sailor piece", "sailor-piece"),
    "escape tsunami": ("escape tsunami", "escape tsunami for brainrots"),
    "rivals": ("rivals",),
    "robux": ("robux",),
}
COLLECTION_LABELS = {
    "adopt me": "Adopt Me",
    "grow a garden": "Grow a Garden",
    "grow a garden 2": "Grow a Garden 2",
    "steal a brainrot": "Steal a Brainrot",
    "murder mystery 2": "Murder Mystery 2",
    "blox fruits": "Blox Fruits",
    "pet simulator 99": "Pet Simulator 99",
    "sailor piece": "Sailor Piece",
    "escape tsunami": "Escape Tsunami",
    "rivals": "Rivals",
    "robux": "Robux",
}


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
        token_filters = [
            condition
            for t in fuzzy_tokens if len(t) >= 2
            for condition in (
                Product.normalized_title.ilike(f"%{t}%"),
                Product.category.ilike(f"%{t}%"),
            )
        ]
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


@search_router.get("/suggestions", response_model=SearchSuggestionsResponse)
async def search_suggestions(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    query_base_hint = _query_market_base_hint(q)
    rows = await _search_candidate_rows(db, q, SEARCH_CANDIDATE_LIMIT)
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
            "category": identity["collection_label"] if identity["collection"] != "unknown" else product.category,
            "representative_product_id": product.id,
            "best_price": product.current_price,
            "currency": product.currency,
            "image_url": product.image_url,
            "competitors": set(),
            "variants": set(),
            "match_score": score,
            "prices_by_currency": {},
        })
        group["competitors"].add(competitor_name)
        group["variants"].add(product.title)
        group["match_score"] = max(group["match_score"], score)
        if product.current_price is not None:
            currency_best = group["prices_by_currency"].get(product.currency)
            if currency_best is None or product.current_price < currency_best:
                group["prices_by_currency"][product.currency] = product.current_price
            # Preserve the familiar single-currency suggestion contract.  When
            # currencies differ, do not compare their numeric values.
            if product.currency == group["currency"] and (
                group["best_price"] is None or product.current_price < group["best_price"]
            ):
                group["best_price"] = product.current_price
                group["representative_product_id"] = product.id
                group["image_url"] = product.image_url

    items = []
    for group in grouped.values():
        item = dict(group)
        if len(group["prices_by_currency"]) > 1:
            item["best_price"] = None
            item["currency"] = "MULTI"
        item["competitors_count"] = len(group["competitors"])
        item["competitors"] = sorted(group["competitors"])
        item["variants"] = sorted(group["variants"])[:5]
        item["match_score"] = round(group["match_score"], 3)
        item["prices_by_currency"] = [
            {"currency": currency, "lowest_observed_price": price}
            for currency, price in sorted(group["prices_by_currency"].items())
        ]
        items.append(item)

    items.sort(key=lambda item: (-item["match_score"], -item["competitors_count"], item["best_price"] is None, item["best_price"] or 0))
    return {
        "items": items[:limit],
        "total": len(items),
        "query": q,
        "candidates_considered": len(rows),
        "candidate_limit_reached": len(rows) == SEARCH_CANDIDATE_LIMIT,
    }


@search_router.get("/compare", response_model=SearchCompareResponse)
async def compare_product(
    q: Optional[str] = Query(default=None, min_length=1, max_length=200),
    product_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    if q is None and product_id is None:
        raise HTTPException(status_code=422, detail="Provide q or product_id.")
    return await _compare_product_response(db, q=q, product_id=product_id)


@search_router.post("/batch-compare")
async def batch_compare_products(
    payload: BatchCompareRequest,
    db: AsyncSession = Depends(get_db),
):
    return await _batch_compare_response(
        db,
        payload.queries,
        include_unmatched=payload.include_unmatched,
    )


@search_router.get("/batch-compare")
async def batch_compare_products_get(
    queries: list[str] = Query(..., min_length=1),
    include_unmatched: bool = True,
    db: AsyncSession = Depends(get_db),
):
    return await _batch_compare_response(
        db,
        _expand_batch_queries(queries),
        include_unmatched=include_unmatched,
    )


@search_router.get("/batch-compare-summary")
async def batch_compare_summary_get(
    queries: list[str] = Query(..., min_length=1),
    format: str = Query("json", pattern="^(json|markdown|csv)$"),
    db: AsyncSession = Depends(get_db),
):
    summary = await _batch_compare_summary_response(db, _expand_batch_queries(queries))
    if format == "markdown":
        return PlainTextResponse(_batch_compare_summary_markdown(summary))
    if format == "csv":
        return PlainTextResponse(_batch_compare_summary_csv(summary), media_type="text/csv")
    return summary


async def _batch_compare_response(
    db: AsyncSession,
    raw_queries: list[str],
    include_unmatched: bool = True,
):
    results = []
    seen = set()
    for raw_query in raw_queries:
        query = raw_query.strip()
        if not query or query in seen:
            continue
        seen.add(query)
        if len(seen) > MAX_BATCH_COMPARE_QUERIES:
            raise HTTPException(
                status_code=422,
                detail=f"Batch compare supports up to {MAX_BATCH_COMPARE_QUERIES} queries per request.",
            )
        result = await _compare_product_response(
            db,
            q=query,
            include_unmatched=include_unmatched,
        )
        result["query"] = query
        results.append(result)
    return {"items": results, "total": len(results)}


def _expand_batch_queries(raw_queries: list[str]) -> list[str]:
    expanded = []
    for raw_query in raw_queries:
        expanded.extend(part.strip() for part in raw_query.replace("\n", ",").split(","))
    return [query for query in expanded if query]


async def _batch_compare_summary_response(db: AsyncSession, raw_queries: list[str]) -> dict:
    queries = _dedupe_batch_queries(raw_queries)
    competitors = (await db.execute(select(Competitor).where(Competitor.active == True))).scalars().all()
    rows = (await db.execute(
        select(Product, Competitor.name.label("cname"))
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(Product.active == True)
    )).all()
    contexts = _build_loaded_product_contexts(rows)

    items = []
    for query in queries:
        target_product = _select_batch_target_from_contexts(contexts, query)
        if not target_product:
            items.append({
                "query": query,
                "matched_item": None,
                "category": None,
                "lowest_price": None,
                "lowest_seller": None,
                "total_matches": 0,
                "competitor_prices": [],
            })
            continue

        matches = _compare_target_from_loaded_contexts(target_product, competitors, contexts)
        priced_matches = [item for item in matches if item["price"] is not None]
        lowest = min(priced_matches, key=lambda item: item["price"]) if priced_matches else None
        items.append({
            "query": query,
            "matched_item": target_product.title,
            "category": target_product.category,
            "lowest_price": lowest["price"] if lowest else None,
            "lowest_seller": lowest["competitor"] if lowest else None,
            "currency": lowest["currency"] if lowest else None,
            "total_matches": len(matches),
            "competitor_prices": matches,
        })

    return {"items": items, "total": len(items)}


def _dedupe_batch_queries(raw_queries: list[str]) -> list[str]:
    queries = []
    seen = set()
    for raw_query in raw_queries:
        query = raw_query.strip()
        if not query or query in seen:
            continue
        seen.add(query)
        if len(queries) >= MAX_BATCH_COMPARE_QUERIES:
            raise HTTPException(
                status_code=422,
                detail=f"Batch compare supports up to {MAX_BATCH_COMPARE_QUERIES} queries per request.",
            )
        queries.append(query)
    return queries


def _build_loaded_product_contexts(rows) -> list[dict]:
    contexts = []
    for product, competitor_name in rows:
        identity = _product_market_identity(product)
        contexts.append({
            "product": product,
            "competitor_name": competitor_name,
            "haystack": f"{product.normalized_title or ''} {normalize_title(product.category or '')}",
            "identity": identity,
            "aliases": _comparison_aliases(identity["base"]),
            "price": float(product.current_price) if product.current_price is not None else None,
        })
    return contexts


def _select_batch_target_from_contexts(contexts: list[dict], q: str):
    candidates = _filter_loaded_candidate_contexts(contexts, q)
    ranked = sorted(
        ((context["product"], _target_selection_score(q, context["product"])) for context in candidates),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[0][0] if ranked else None


def _filter_loaded_candidate_contexts(contexts: list[dict], q: str) -> list[dict]:
    norm = normalize_title(q)
    tokens = norm.split()
    if not tokens:
        return contexts
    fuzzy_tokens = {t for token in tokens for t in _fuzzy_token_variants(token) if len(t) >= 2}
    if not fuzzy_tokens:
        return contexts
    return [
        context for context in contexts
        if any(token in context["haystack"] for token in fuzzy_tokens)
    ]


def _compare_target_from_loaded_contexts(target_product: Product, competitors: list[Competitor], contexts: list[dict]) -> list[dict]:
    target_identity = _product_market_identity(target_product)
    aliases = _comparison_aliases(target_identity["base"])
    best_by_competitor = {}
    for context in contexts:
        product = context["product"]
        candidate_identity = (
            _product_market_identity(product, base_hint=target_identity["base"])
            if target_identity["collection"] == "steal a brainrot"
            else context["identity"]
        )
        if not _collections_compatible(target_identity, candidate_identity):
            continue
        if candidate_identity["mutation"] != target_identity["mutation"]:
            continue
        candidate_aliases = (
            _comparison_aliases(candidate_identity["base"])
            if target_identity["collection"] == "steal a brainrot"
            else context["aliases"]
        )
        score = max(
            _comparison_score(target_alias, candidate_alias)
            for target_alias in aliases
            for candidate_alias in candidate_aliases
        )
        if score < 0.86:
            continue
        price = context["price"]
        current = best_by_competitor.get(product.competitor_id)
        if not current or score > current["match_score"] or (
            score == current["match_score"] and price is not None and (
                current["price"] is None or price < current["price"]
            )
        ):
            best_by_competitor[product.competitor_id] = {
                "competitor": context["competitor_name"],
                "item": product.title,
                "category": product.category,
                "price": price,
                "currency": product.currency,
                "url": product.url,
                "match_score": round(score, 3),
            }

    matches = [best_by_competitor[competitor.id] for competitor in competitors if competitor.id in best_by_competitor]
    matches.sort(key=lambda item: (item["price"] is None, item["price"] or 0, item["competitor"]))
    return matches


def _batch_compare_summary_markdown(summary: dict) -> str:
    lines = [
        "| Product | Matched item | Category | Lowest price | Lowest seller | Competitor prices |",
        "|---|---|---|---:|---|---|",
    ]
    for item in summary["items"]:
        lowest_price = _format_price(item.get("lowest_price"), item.get("currency"))
        competitor_prices = "; ".join(
            f"{price['competitor']}: {_format_price(price.get('price'), price.get('currency'))}"
            for price in item.get("competitor_prices", [])
        ) or "No match"
        lines.append("| " + " | ".join(
            _escape_markdown_table(value)
            for value in (
                item.get("query"),
                item.get("matched_item") or "No match",
                item.get("category") or "",
                lowest_price,
                item.get("lowest_seller") or "",
                competitor_prices,
            )
        ) + " |")
    return "\n".join(lines) + "\n"


def _batch_compare_summary_csv(summary: dict) -> str:
    lines = ["Product,Matched item,Category,Lowest price,Lowest seller,Competitor prices"]
    for item in summary["items"]:
        competitor_prices = "; ".join(
            f"{price['competitor']}: {_format_price(price.get('price'), price.get('currency'))}"
            for price in item.get("competitor_prices", [])
        )
        values = [
            item.get("query"),
            item.get("matched_item") or "No match",
            item.get("category") or "",
            _format_price(item.get("lowest_price"), item.get("currency")),
            item.get("lowest_seller") or "",
            competitor_prices,
        ]
        lines.append(",".join(_escape_csv_value(value) for value in values))
    return "\n".join(lines) + "\n"


def _format_price(price, currency: Optional[str]) -> str:
    if price is None:
        return "N/A"
    symbol = "$" if currency == "USD" else f"{currency or ''} "
    return f"{symbol}{float(price):.2f}"


def _escape_markdown_table(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _escape_csv_value(value) -> str:
    text = str(value or "")
    return '"' + text.replace('"', '""') + '"'


async def _compare_product_response(
    db: AsyncSession,
    q: Optional[str] = None,
    product_id: Optional[int] = None,
    include_unmatched: bool = True,
):
    target_product = None
    if product_id is not None:
        result = await db.execute(select(Product).where(Product.id == product_id))
        target_product = result.scalar_one_or_none()
    if not target_product and q:
        rows = await _search_candidate_rows(db, q, 1000)
        ranked = sorted(
            ((product, _target_selection_score(q, product)) for product, _ in rows),
            key=lambda item: item[1],
            reverse=True,
        )
        target_product = ranked[0][0] if ranked else None
    if not target_product:
        return {
            "target": None,
            "items": [],
            "total_matches": 0,
            "market_summary": _empty_market_summary(),
        }

    target_identity = _product_market_identity(target_product)
    aliases = _comparison_aliases(target_identity["base"])
    competitor_rows = await _competitors_with_search_evidence(db)
    competitors = [row[0] for row in competitor_rows]
    evidence_ids = {
        row[0].id: {
            "complete": row.latest_complete_id,
            "partial": row.latest_partial_id,
            "failed": row.latest_failed_id,
            "terminal": row.latest_terminal_id,
            "active": row.active_run_id,
        }
        for row in competitor_rows
    }
    rows = await _comparison_fast_path_rows(db, target_identity)

    best_by_competitor = {}
    identity_cache: dict[tuple[str, str | None], dict] = {}

    def consider(candidate_rows) -> None:
        for product, competitor_name in candidate_rows:
            identity_key = (product.normalized_title, product.category)
            candidate_identity = identity_cache.get(identity_key)
            if candidate_identity is None:
                candidate_identity = _product_market_identity(
                    product, base_hint=target_identity["base"]
                )
                identity_cache[identity_key] = candidate_identity
            if not _collections_compatible(target_identity, candidate_identity):
                continue
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
                    current["product"]["current_price"] is None
                    or product.current_price < current["product"]["current_price"]
                )
            ):
                item = ProductOut.model_validate(product).model_dump()
                item["competitor_name"] = competitor_name
                best_by_competitor[product.competitor_id] = {
                    "competitor_id": product.competitor_id,
                    "competitor_name": competitor_name,
                    "match_score": round(score, 3),
                    "raw_match_score": score,
                    "product_model": product,
                    "product": item,
                }

    consider(rows)
    unresolved_competitor_ids = [
        competitor.id
        for competitor in competitors
        if (
            competitor.id not in best_by_competitor
            or best_by_competitor[competitor.id]["raw_match_score"] < 1.0
        )
    ]
    if unresolved_competitor_ids:
        for competitor_id in unresolved_competitor_ids:
            best_by_competitor.pop(competitor_id, None)
        consider(await _comparison_fallback_rows(db, unresolved_competitor_ids))

    evidence_run_ids = {
        run_id
        for values in evidence_ids.values()
        for run_id in values.values()
        if run_id is not None
    }
    producing_run_ids = {
        match["product_model"].last_observed_run_id
        for match in best_by_competitor.values()
        if match["product_model"].last_observed_run_id is not None
    }
    runs_by_id = await _load_search_runs(db, evidence_run_ids | producing_run_ids)
    snapshots_by_product = await _load_recent_price_snapshots(
        db,
        [match["product_model"].id for match in best_by_competitor.values()],
    )

    now = datetime.now(timezone.utc)
    items = []
    for competitor in competitors:
        match = best_by_competitor.get(competitor.id)
        ids = evidence_ids[competitor.id]
        evidence = CompetitorEvidence(
            latest_complete=_run_evidence(runs_by_id.get(ids["complete"])),
            latest_partial=_run_evidence(runs_by_id.get(ids["partial"])),
            latest_failed=_run_evidence(runs_by_id.get(ids["failed"])),
            latest_terminal=_run_evidence(runs_by_id.get(ids["terminal"])),
            active_run=_run_evidence(runs_by_id.get(ids["active"])),
        )
        product = match["product_model"] if match else None
        assessment = assess_search_trust(
            evidence=evidence,
            product_observed_at=product.last_observed_at if product else None,
            product_run_id=product.last_observed_run_id if product else None,
            has_price=bool(product and product.current_price is not None),
            stock_status=product.stock_status if product else None,
            now=now,
        )
        latest_terminal = evidence.latest_terminal
        trust = {
            "coverage_state": assessment.coverage_state,
            "price_reliability": assessment.price_reliability,
            "reliable": assessment.reliable,
            "trustworthy_current_observation": assessment.trustworthy_current_observation,
            "product_observed_at": product.last_observed_at if product else None,
            "product_observation_age_seconds": assessment.product_observation_age_seconds,
            "latest_complete_at": (
                evidence.latest_complete.observation_completed_at
                if evidence.latest_complete else None
            ),
            "complete_coverage_age_seconds": assessment.complete_coverage_age_seconds,
            "latest_partial_at": (
                evidence.latest_partial.observation_completed_at
                if evidence.latest_partial else None
            ),
            "last_failed_at": (
                evidence.latest_failed.terminal_at if evidence.latest_failed else None
            ),
            "required_cycle_date": assessment.required_cycle_date.isoformat(),
            "current_completeness": latest_terminal.completeness if latest_terminal else "unknown",
            "warning": assessment.warning,
            "producing_run": _serialize_run_evidence(
                _run_evidence(runs_by_id.get(product.last_observed_run_id)) if product else None
            ),
            "latest_complete_run": _serialize_run_evidence(evidence.latest_complete),
            "latest_partial_run": _serialize_run_evidence(evidence.latest_partial),
            "latest_failed_run": _serialize_run_evidence(evidence.latest_failed),
            "active_sync": _serialize_run_evidence(evidence.active_run),
        }
        items.append({
            "competitor_id": competitor.id,
            "competitor_name": competitor.name,
            "match_score": match["match_score"] if match else 0,
            "product": match["product"] if match else None,
            "trust": trust,
            "price_change": (
                _price_change_context(product, snapshots_by_product.get(product.id, []))
                if product else None
            ),
            "reference_currency": None,
            "difference_from_reliable_low": None,
            "difference_percentage": None,
        })

    if not include_unmatched:
        items = [item for item in items if item["product"]]
    market_summary = _market_summary(items)
    reliable_low_by_currency = {
        summary["currency"]: summary["lowest_reliable_price"]
        for summary in market_summary["currencies"]
        if summary["lowest_reliable_price"] is not None
    }
    for item in items:
        product = item["product"]
        if not product or product["current_price"] is None:
            continue
        currency = product["currency"]
        reference = reliable_low_by_currency.get(currency)
        if reference is None:
            continue
        difference = product["current_price"] - reference
        item["reference_currency"] = currency
        item["difference_from_reliable_low"] = difference
        item["difference_percentage"] = (
            round(float(difference / reference * 100), 2) if reference else None
        )

    items.sort(key=lambda item: (
        not item["trust"]["reliable"],
        item["product"] is None,
        item["product"]["current_price"] is None if item["product"] else True,
        item["product"]["current_price"] if item["product"] else 0,
        item["competitor_name"],
    ))
    target = ProductOut.model_validate(target_product).model_dump()
    return {
        "target": target,
        "identity": target_identity,
        "aliases": sorted(aliases),
        "items": items,
        "total_matches": sum(1 for item in items if item["product"]),
        "market_summary": market_summary,
    }


async def _comparison_fast_path_rows(
    db: AsyncSession,
    target_identity: dict,
):
    """Find likely exact-alias rows cheaply before the compatibility fallback.

    This filter is intentionally not treated as exhaustive.  Any competitor without
    a score-1 match is searched by ``_comparison_fallback_rows``, preserving the
    regression-protected collection/mutation/fuzzy matcher exactly.
    """

    descriptor_tokens = {
        "knife", "knive", "knives", "gun", "guns", "weapon", "weapons", "godly", "mm2"
    }
    base_tokens = [
        token for token in target_identity["base"].split()
        if len(token) >= 3 and token not in descriptor_tokens
    ]
    prefixes = sorted(
        {token if len(token) <= 5 else token[:5] for token in base_tokens},
        key=lambda token: (-len(token), token),
    )[:3]
    query = (
        select(Product, Competitor.name.label("cname"))
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(Product.active == True, Competitor.active == True)
    )
    if prefixes:
        query = query.where(or_(*[
            Product.normalized_title.ilike(f"%{prefix}%") for prefix in prefixes
        ]))
    return (await db.execute(query)).all()


async def _comparison_fallback_rows(
    db: AsyncSession,
    competitor_ids: list[int],
):
    return (await db.execute(
        select(Product, Competitor.name.label("cname"))
        .join(Competitor, Product.competitor_id == Competitor.id)
        .where(
            Product.active == True,
            Competitor.active == True,
            Product.competitor_id.in_(competitor_ids),
        )
    )).all()


async def _competitors_with_search_evidence(db: AsyncSession):
    def latest_id(*conditions, order_by):
        return (
            select(ScrapeRun.id)
            .where(ScrapeRun.competitor_id == Competitor.id, *conditions)
            .order_by(*order_by)
            .limit(1)
            .correlate(Competitor)
            .scalar_subquery()
        )

    complete_id = latest_id(
        ScrapeRun.status == "success",
        ScrapeRun.completeness == "complete",
        order_by=(ScrapeRun.observation_completed_at.desc().nullslast(), ScrapeRun.id.desc()),
    )
    partial_id = latest_id(
        ScrapeRun.status == "success",
        ScrapeRun.completeness.in_(("partial", "suspicious_empty")),
        order_by=(ScrapeRun.observation_completed_at.desc().nullslast(), ScrapeRun.id.desc()),
    )
    failed_id = latest_id(
        ScrapeRun.status.in_(("failed", "abandoned")),
        order_by=(ScrapeRun.terminal_at.desc().nullslast(), ScrapeRun.id.desc()),
    )
    terminal_id = latest_id(
        ScrapeRun.status.in_(("success", "failed", "abandoned")),
        order_by=(ScrapeRun.terminal_at.desc().nullslast(), ScrapeRun.id.desc()),
    )
    active_id = latest_id(
        ScrapeRun.status.in_(("queued", "running", "retry_wait")),
        order_by=(ScrapeRun.queued_at.desc(), ScrapeRun.id.desc()),
    )
    return (await db.execute(
        select(
            Competitor,
            complete_id.label("latest_complete_id"),
            partial_id.label("latest_partial_id"),
            failed_id.label("latest_failed_id"),
            terminal_id.label("latest_terminal_id"),
            active_id.label("active_run_id"),
        )
        .where(Competitor.active == True)
        .order_by(Competitor.name)
    )).all()


async def _load_search_runs(db: AsyncSession, run_ids: set[int]) -> dict[int, ScrapeRun]:
    if not run_ids:
        return {}
    runs = (await db.execute(select(ScrapeRun).where(ScrapeRun.id.in_(run_ids)))).scalars().all()
    return {run.id: run for run in runs}


async def _load_recent_price_snapshots(
    db: AsyncSession,
    product_ids: list[int],
) -> dict[int, list[ProductSnapshot]]:
    if not product_ids:
        return {}
    snapshot_order = func.coalesce(
        ProductSnapshot.observed_at, ProductSnapshot.checked_at
    ).desc()
    ranked = (
        select(
            ProductSnapshot.id.label("snapshot_id"),
            func.row_number().over(
                partition_by=ProductSnapshot.product_id,
                order_by=(snapshot_order, ProductSnapshot.id.desc()),
            ).label("snapshot_rank"),
        )
        .where(ProductSnapshot.product_id.in_(product_ids))
        .subquery()
    )
    snapshots = (await db.execute(
        select(ProductSnapshot)
        .join(ranked, ranked.c.snapshot_id == ProductSnapshot.id)
        .where(ranked.c.snapshot_rank <= 10)
        .order_by(
            ProductSnapshot.product_id,
            snapshot_order,
            ProductSnapshot.id.desc(),
        )
    )).scalars().all()
    by_product: dict[int, list[ProductSnapshot]] = {}
    for snapshot in snapshots:
        by_product.setdefault(snapshot.product_id, []).append(snapshot)
    return by_product


def _run_evidence(run: ScrapeRun | None) -> RunEvidence | None:
    if run is None:
        return None
    return RunEvidence(
        run_id=run.id,
        status=run.status,
        completeness=run.completeness,
        observation_completed_at=run.observation_completed_at,
        terminal_at=run.terminal_at,
        failure_category=run.failure_category,
        failure_reason=run.error_message,
    )


def _serialize_run_evidence(run: RunEvidence | None) -> dict | None:
    if run is None:
        return None
    return {
        "run_id": run.run_id,
        "status": run.status,
        "completeness": run.completeness,
        "observation_completed_at": run.observation_completed_at,
        "terminal_at": run.terminal_at,
        "failure_category": run.failure_category,
        "failure_reason": run.failure_reason,
    }


def _price_change_context(
    product: Product,
    snapshots: list[ProductSnapshot],
) -> dict | None:
    if product.current_price is None:
        return None
    current_index = next((
        index for index, snapshot in enumerate(snapshots)
        if snapshot.price == product.current_price and snapshot.currency == product.currency
    ), None)
    if current_index is None:
        return None
    current_snapshot = snapshots[current_index]
    previous = next((
        snapshot for snapshot in snapshots[current_index + 1:]
        if snapshot.price is not None
        and snapshot.currency == product.currency
        and snapshot.price != product.current_price
    ), None)
    if previous is None or previous.price is None:
        return None
    difference = product.current_price - previous.price
    changed_at = current_snapshot.observed_at or current_snapshot.checked_at
    return {
        "previous_price": previous.price,
        "current_price": product.current_price,
        "currency": product.currency,
        "direction": "increase" if difference > 0 else "decrease",
        "amount": abs(difference),
        "percentage": (
            round(abs(float(difference / previous.price * 100)), 2)
            if previous.price else None
        ),
        "changed_at": changed_at,
        "scrape_run_id": current_snapshot.scrape_run_id,
    }


def _market_summary(items: list[dict]) -> dict:
    priced_by_currency: dict[str, list[dict]] = {}
    for item in items:
        product = item["product"]
        if product and product["current_price"] is not None:
            priced_by_currency.setdefault(product["currency"], []).append(item)

    currencies = []
    for currency, priced_items in sorted(priced_by_currency.items()):
        reliable_items = [item for item in priced_items if item["trust"]["reliable"]]
        observed_low = min(priced_items, key=lambda item: item["product"]["current_price"])
        reliable_low = (
            min(reliable_items, key=lambda item: item["product"]["current_price"])
            if reliable_items else None
        )
        reliable_prices = sorted(item["product"]["current_price"] for item in reliable_items)
        currencies.append({
            "currency": currency,
            "lowest_reliable_price": (
                reliable_low["product"]["current_price"] if reliable_low else None
            ),
            "lowest_reliable_competitor_id": reliable_low["competitor_id"] if reliable_low else None,
            "lowest_reliable_competitor_name": reliable_low["competitor_name"] if reliable_low else None,
            "lowest_observed_price": observed_low["product"]["current_price"],
            "lowest_observed_competitor_id": observed_low["competitor_id"],
            "lowest_observed_competitor_name": observed_low["competitor_name"],
            "highest_reliable_price": reliable_prices[-1] if reliable_prices else None,
            "median_reliable_price": _decimal_median(reliable_prices),
            "observed_price_count": len(priced_items),
            "reliable_price_count": len(reliable_items),
        })

    matched = [item for item in items if item["product"]]
    trustworthy = [
        item for item in matched if item["trust"]["trustworthy_current_observation"]
    ]
    return {
        "currencies": currencies,
        "competitors_carrying": len(matched),
        "trustworthy_current": len(trustworthy),
        "degraded_or_unknown": len(matched) - len(trustworthy),
        "syncing_competitors": sum(1 for item in items if item["trust"]["active_sync"]),
        "no_reliable_prices": not any(item["trust"]["reliable"] for item in matched),
    }


def _decimal_median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal("2")


def _empty_market_summary() -> dict:
    return {
        "currencies": [],
        "competitors_carrying": 0,
        "trustworthy_current": 0,
        "degraded_or_unknown": 0,
        "syncing_competitors": 0,
        "no_reliable_prices": True,
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
        token_filters = [
            condition
            for t in fuzzy_tokens if len(t) >= 2
            for condition in (
                Product.normalized_title.ilike(f"%{t}%"),
                Product.category.ilike(f"%{t}%"),
            )
        ]
        if token_filters:
            query = query.where(or_(*token_filters))
    query = query.order_by(Product.last_checked_at.desc(), Product.id.desc()).limit(limit)
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


def _target_selection_score(query: str, product: Product) -> float:
    query_norm = normalize_title(query)
    title_norm = product.normalized_title or ""
    if not query_norm or not title_norm:
        return 0

    query_tokens = query_norm.split()
    title_tokens = title_norm.split()
    query_base = _query_market_base_hint(query)
    identity = _product_market_identity(product, base_hint=query_base)
    score = max(
        _comparison_score(query_norm, title_norm),
        _comparison_score(query_base, identity["base"]),
    )
    if title_norm == query_norm:
        score += 2
    if identity["base"] == query_base:
        score += 1
    score -= abs(len(title_tokens) - len(query_tokens)) * 0.05

    variant_prefixes = {"chroma"}
    query_prefix = query_tokens[0] if query_tokens else ""
    title_prefix = title_tokens[0] if title_tokens else ""
    if title_prefix in variant_prefixes and query_prefix != title_prefix:
        score -= 0.7
    if query_prefix in variant_prefixes and title_prefix != query_prefix:
        score -= 0.7
    return score


def _product_market_identity(product: Product, base_hint: Optional[str] = None) -> dict:
    return _market_identity(
        product.normalized_title,
        product.category,
        base_hint=base_hint,
    )


def _market_identity(normalized_title: str, category: Optional[str] = None, base_hint: Optional[str] = None) -> dict:
    cleaned = _clean_market_tokens(normalized_title)
    collection = _collection_identity(normalized_title, category)
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
        "key": f"{collection['key']}::{base}::{mutation}",
        "item_key": f"{base}::{mutation}",
        "collection": collection["key"],
        "collection_label": collection["label"],
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
    if _has_brainrot_stat_token(tokens):
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


def _collection_identity(normalized_title: str, category: Optional[str]) -> dict:
    category_norm = normalize_title(category or "")
    combined = f"{category_norm} {normalized_title}".strip()
    if any(marker in combined for marker in BRAINROT_CATEGORY_MARKERS) or _has_brainrot_stat_token(normalized_title.split()):
        return {"key": "steal a brainrot", "label": _collection_label("steal a brainrot")}
    for canonical, aliases in COLLECTION_ALIASES.items():
        for alias in aliases:
            alias_norm = normalize_title(alias)
            if _collection_alias_matches(alias_norm, category_norm, combined):
                return {"key": canonical, "label": _collection_label(canonical)}
    if category_norm and category_norm not in GENERIC_COLLECTION_TOKENS:
        return {"key": category_norm, "label": _title_from_normalized(category_norm)}
    return {"key": "unknown", "label": "Unknown"}


def _collection_label(canonical: str) -> str:
    return COLLECTION_LABELS.get(canonical, _title_from_normalized(canonical))


def _collection_alias_matches(alias: str, category_norm: str, combined: str) -> bool:
    if not alias:
        return False
    if alias == category_norm:
        return True
    if f" {alias} " in f" {combined} ":
        return True
    compact_alias = alias.replace(" ", "")
    compact_category = category_norm.replace(" ", "")
    return bool(compact_alias and compact_alias == compact_category)


def _collections_compatible(target_identity: dict, candidate_identity: dict) -> bool:
    target_collection = target_identity.get("collection") or "unknown"
    candidate_collection = candidate_identity.get("collection") or "unknown"
    if "unknown" in {target_collection, candidate_collection}:
        return True
    return target_collection == candidate_collection


def _has_brainrot_stat_token(tokens: list[str]) -> bool:
    return any(any(ch.isdigit() for ch in token) and any(unit in token for unit in ("m", "b", "qn")) for token in tokens)


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
