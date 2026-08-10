import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Competitor, Product
from app.services.scraper import scrape_competitor
from app.utils.text_normalizer import normalize_title

router = APIRouter(prefix="/api/exports", tags=["exports"])

EXPORT_FIELDS = [
    "competitor_name",
    "competitor_base_url",
    "collection_url",
    "category",
    "title",
    "price",
    "currency",
    "stock_status",
    "sku",
    "external_id",
    "product_url",
    "image_url",
    "scraped_at",
]


@router.get("/collection-prices")
async def export_collection_prices(
    competitor_id: int,
    collection_url: str = Query(..., min_length=1),
    format: str = Query("csv", pattern="^(csv|jsonl|json)$"),
    max_pages: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    competitor = (await db.execute(
        select(Competitor).where(Competitor.id == competitor_id)
    )).scalar_one_or_none()
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    clean_collection_url = collection_url.strip()
    _validate_collection_url(competitor, clean_collection_url)

    scrape_payload = _collection_scrape_payload(competitor, clean_collection_url, max_pages)
    products = await scrape_competitor(
        scrape_payload,
        max_pages=max_pages,
        page_delay=settings.DEFAULT_PAGE_DELAY_SECONDS,
        headless=settings.PLAYWRIGHT_HEADLESS,
        user_agent=settings.USER_AGENT,
    )
    if not products:
        products = await _saved_collection_products(db, competitor, clean_collection_url)
    rows = _export_rows(competitor, clean_collection_url, products)
    filename = _export_filename(competitor.name, clean_collection_url, format)

    if format == "jsonl":
        body = "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in rows)
        if body:
            body += "\n"
        return Response(
            body,
            media_type="application/x-ndjson; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "json":
        body = json.dumps({
            "competitor": competitor.name,
            "collection_url": clean_collection_url,
            "products_count": len(rows),
            "items": rows,
        }, ensure_ascii=False, default=str, indent=2)
        return Response(
            body,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return Response(
        _csv_body(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _collection_scrape_payload(competitor: Competitor, collection_url: str, max_pages: int) -> dict:
    selector_config = dict(competitor.selector_config or {})
    selector_config["discover_collections"] = False
    selector_config["include_all_products"] = False
    selector_config["request_timeout_seconds"] = 8
    selector_config["max_sitemap_products"] = max(max_pages * 250, 250)

    handle = _collection_handle(collection_url)
    if handle:
        selector_config["collection_handles"] = [handle]
        selector_config["prefer_storefront_graphql"] = False

    return {
        "id": competitor.id,
        "base_url": competitor.base_url,
        "listing_urls": [collection_url],
        "selector_config": selector_config,
        "scrape_type": competitor.scrape_type or "shopify_json",
    }


def _export_rows(competitor: Competitor, collection_url: str, products: list[dict]) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for product in products:
        rows.append({
            "competitor_name": competitor.name,
            "competitor_base_url": competitor.base_url,
            "collection_url": collection_url,
            "category": product.get("category"),
            "title": product.get("title"),
            "price": product.get("price"),
            "currency": product.get("currency") or "USD",
            "stock_status": product.get("stock_status") or "unknown",
            "sku": product.get("sku"),
            "external_id": product.get("external_id"),
            "product_url": product.get("url"),
            "image_url": product.get("image_url"),
            "scraped_at": scraped_at,
        })
    rows.sort(key=lambda row: ((row["title"] or "").lower(), row["price"] is None, row["price"] or 0))
    return rows


async def _saved_collection_products(db: AsyncSession, competitor: Competitor, collection_url: str) -> list[dict]:
    result = await db.execute(
        select(Product)
        .where(Product.competitor_id == competitor.id, Product.active == True)
        .order_by(Product.title.asc())
    )
    products = []
    for product in result.scalars().all():
        if not _product_matches_collection(product, collection_url):
            continue
        products.append({
            "title": product.title,
            "price": float(product.current_price) if product.current_price is not None else None,
            "currency": product.currency,
            "url": product.url,
            "image_url": product.image_url,
            "stock_status": product.stock_status,
            "sku": product.sku,
            "external_id": product.external_id,
            "category": product.category,
        })
    return products


def _product_matches_collection(product: Product, collection_url: str) -> bool:
    aliases = _collection_aliases(collection_url)
    category = normalize_title(product.category or "")
    title = normalize_title(product.title or "")
    haystack = f"{category} {title}".strip()
    return any(_collection_alias_matches(alias, category, haystack) for alias in aliases)


def _collection_aliases(collection_url: str) -> list[str]:
    handle = _collection_handle(collection_url) or ""
    normalized = normalize_title(handle.replace("-", " "))
    aliases = [normalized]
    if "brainrot" in normalized:
        aliases.extend(["brainrot", "brainrots", "sab"])
    if "murder mystery" in normalized or normalized == "mm2":
        aliases.extend(["murder mystery 2", "mm2"])
    if "grow a garden" in normalized or normalized in {"gag", "gag2"}:
        aliases.extend(["grow a garden", "grow a garden 2", "gag", "gag2"])
    if "adopt me" in normalized or normalized == "adm":
        aliases.extend(["adopt me", "adm"])
    if "blox fruit" in normalized:
        aliases.extend(["blox fruits", "blox fruit"])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _collection_alias_matches(alias: str, category: str, haystack: str) -> bool:
    if not alias:
        return False
    if alias == category:
        return True
    if f" {alias} " in f" {haystack} ":
        return True
    compact_alias = alias.replace(" ", "")
    compact_category = category.replace(" ", "")
    return bool(compact_alias and compact_alias == compact_category)


def _csv_body(rows: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _validate_collection_url(competitor: Competitor, collection_url: str) -> None:
    parsed = urlparse(collection_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Collection URL must be an absolute http(s) URL")

    competitor_host = urlparse(competitor.base_url).netloc.lower().removeprefix("www.")
    collection_host = parsed.netloc.lower().removeprefix("www.")
    if competitor_host and collection_host != competitor_host:
        raise HTTPException(status_code=400, detail="Collection URL must belong to the selected competitor")


def _collection_handle(collection_url: str) -> Optional[str]:
    match = re.search(r"/collections/([^/?#]+)", collection_url or "")
    return match.group(1) if match else None


def _export_filename(competitor_name: str, collection_url: str, format: str) -> str:
    handle = _collection_handle(collection_url) or "collection"
    extension = "jsonl" if format == "jsonl" else format
    safe_name = re.sub(r"[^a-z0-9]+", "-", competitor_name.lower()).strip("-") or "competitor"
    safe_handle = re.sub(r"[^a-z0-9]+", "-", handle.lower()).strip("-") or "collection"
    return f"{safe_name}-{safe_handle}-prices.{extension}"
