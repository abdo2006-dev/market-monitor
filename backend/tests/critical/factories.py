"""
Deterministic fixture builders for the daily-critical-path suite.

No network, no randomness, no reliance on wall-clock "now" beyond what the code
under test uses. Everything here writes to the test database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from app.models import Competitor, Event, Product, ProductSnapshot, ScrapeRun

# Fixture baseline clock. Must be in the PAST relative to real wall-clock time,
# because the code under test stamps rows with datetime.now(timezone.utc) and
# several assertions check that freshness advances. A fixed future constant
# would make those comparisons run backwards.
NOW = datetime.now(timezone.utc) - timedelta(days=1)


async def make_competitor(
    session,
    name: str = "TestStore",
    base_url: str = "https://teststore.example",
    *,
    scrape_type: str = "shopify_json",
    active: bool = True,
    listing_urls: Optional[list[str]] = None,
    selector_config: Optional[dict] = None,
    discord_webhook_url: Optional[str] = None,
    scan_frequency_minutes: int = 60,
    last_scan_at: Optional[datetime] = None,
    last_scan_status: Optional[str] = None,
    category: Optional[str] = None,
) -> Competitor:
    competitor = Competitor(
        name=name,
        base_url=base_url,
        category=category,
        active=active,
        scan_frequency_minutes=scan_frequency_minutes,
        scrape_type=scrape_type,
        listing_urls=listing_urls or [],
        selector_config=selector_config or {},
        discord_webhook_url=discord_webhook_url,
        last_scan_at=last_scan_at,
        last_scan_status=last_scan_status,
    )
    session.add(competitor)
    await session.flush()
    await session.refresh(competitor)
    return competitor


async def make_product(
    session,
    competitor: Competitor,
    title: str,
    *,
    price: Optional[str | float | Decimal] = None,
    currency: str = "USD",
    url: Optional[str] = None,
    external_id: Optional[str] = None,
    category: Optional[str] = None,
    stock_status: str = "in_stock",
    active: bool = True,
    last_checked_at: Optional[datetime] = None,
    consecutive_misses: int = 0,
    sku: Optional[str] = None,
    image_url: Optional[str] = None,
) -> Product:
    from app.utils.text_normalizer import normalize_title

    checked = last_checked_at or NOW
    product = Product(
        competitor_id=competitor.id,
        external_id=external_id,
        title=title,
        normalized_title=normalize_title(title),
        category=category,
        url=url or f"{competitor.base_url}/products/{normalize_title(title).replace(' ', '-')}",
        image_url=image_url,
        current_price=Decimal(str(price)) if price is not None else None,
        currency=currency,
        stock_status=stock_status,
        sku=sku,
        first_seen_at=checked,
        last_seen_at=checked,
        last_checked_at=checked,
        active=active,
        consecutive_misses=consecutive_misses,
    )
    session.add(product)
    await session.flush()
    await session.refresh(product)
    return product


def observation(
    title: str,
    *,
    price: Optional[float] = None,
    url: str = "",
    currency: str = "USD",
    stock_status: str = "in_stock",
    category: Optional[str] = None,
    external_id: Optional[str] = None,
    sku: Optional[str] = None,
    image_url: Optional[str] = None,
) -> dict:
    """
    One scraped product, in the shape services.scraper currently returns.

    This mirrors the untyped dict contract documented in
    docs/SCRAPING_ARCHITECTURE.md §1.1 — deliberately, so these tests
    characterise the real interface rather than an idealised one.
    """
    return {
        "title": title,
        "price": price,
        "currency": currency,
        "url": url,
        "image_url": image_url,
        "stock_status": stock_status,
        "sku": sku,
        "external_id": external_id,
        "category": category,
    }


async def counts(session, competitor_id: int) -> dict:
    """Row counts for a competitor, for concise assertions."""
    from sqlalchemy import func, select

    async def _count(model, *where):
        stmt = select(func.count(model.id)).where(*where)
        return (await session.execute(stmt)).scalar() or 0

    product_ids = (
        await session.execute(
            select(Product.id).where(Product.competitor_id == competitor_id)
        )
    ).scalars().all()

    return {
        "products": len(product_ids),
        "active_products": await _count(
            Product, Product.competitor_id == competitor_id, Product.active.is_(True)
        ),
        "snapshots": (
            await _count(ProductSnapshot, ProductSnapshot.product_id.in_(product_ids))
            if product_ids
            else 0
        ),
        "events": await _count(Event, Event.competitor_id == competitor_id),
        "runs": await _count(ScrapeRun, ScrapeRun.competitor_id == competitor_id),
    }


async def event_types(session, competitor_id: int) -> list[str]:
    from sqlalchemy import select

    rows = await session.execute(
        select(Event.event_type)
        .where(Event.competitor_id == competitor_id)
        .order_by(Event.id)
    )
    return list(rows.scalars().all())


def hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)
