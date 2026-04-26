import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models import Product, ProductSnapshot, Event, Competitor
from app.utils.text_normalizer import normalize_title

logger = logging.getLogger(__name__)

CONSECUTIVE_MISS_THRESHOLD = 3


async def detect_changes(
    session: AsyncSession,
    competitor: Competitor,
    scraped_products: list[dict],
) -> dict:
    """
    Match scraped products against stored products, create/update records,
    generate events for changes. Returns summary counts.
    """
    now = datetime.now(timezone.utc)
    new_count = 0
    price_change_count = 0

    # Load all existing products for this competitor
    result = await session.execute(
        select(Product).where(Product.competitor_id == competitor.id)
    )
    existing_products = {p.url: p for p in result.scalars().all()}
    scraped_urls = set()

    for item in scraped_products:
        url = item.get("url", "")
        if not url:
            continue
        scraped_urls.add(url)
        norm_title = normalize_title(item.get("title", ""))

        # Match by URL first
        product = existing_products.get(url)

        # Match by external_id / SKU
        if not product and item.get("external_id"):
            for p in existing_products.values():
                if p.external_id == item["external_id"]:
                    product = p
                    break

        # Match by normalized title
        if not product and norm_title:
            for p in existing_products.values():
                if p.normalized_title == norm_title:
                    product = p
                    break

        if not product:
            # New product
            product = Product(
                competitor_id=competitor.id,
                external_id=item.get("external_id"),
                title=item.get("title", ""),
                normalized_title=norm_title,
                url=url,
                image_url=item.get("image_url"),
                current_price=item.get("price"),
                currency=item.get("currency", "USD"),
                stock_status=item.get("stock_status", "unknown"),
                sku=item.get("sku"),
                first_seen_at=now,
                last_seen_at=now,
                last_checked_at=now,
                active=True,
                consecutive_misses=0,
            )
            session.add(product)
            await session.flush()  # Get product.id

            snapshot = ProductSnapshot(
                product_id=product.id,
                title=product.title,
                price=product.current_price,
                currency=product.currency,
                stock_status=product.stock_status,
                image_url=product.image_url,
                checked_at=now,
            )
            session.add(snapshot)

            event = Event(
                competitor_id=competitor.id,
                product_id=product.id,
                event_type="new_product",
                old_value=None,
                new_value={
                    "title": product.title,
                    "price": float(product.current_price) if product.current_price else None,
                    "currency": product.currency,
                    "stock_status": product.stock_status,
                    "url": product.url,
                },
                event_message=f"New product found: {product.title}",
                detected_at=now,
            )
            session.add(event)
            new_count += 1
            existing_products[url] = product
        else:
            # Existing product - check for changes
            changed = False
            snapshot_needed = False

            old_price = product.current_price
            new_price = item.get("price")
            old_stock = product.stock_status
            new_stock = item.get("stock_status", "unknown")

            # Price change
            if _prices_differ(old_price, new_price):
                price_event_type = _get_price_event_type(old_price, new_price)
                old_val = float(old_price) if old_price is not None else None
                new_val = float(new_price) if new_price is not None else None
                diff_amount = (new_val - old_val) if (old_val is not None and new_val is not None) else None
                diff_pct = (diff_amount / old_val * 100) if (old_val and diff_amount is not None) else None

                event = Event(
                    competitor_id=competitor.id,
                    product_id=product.id,
                    event_type=price_event_type,
                    old_value={"price": old_val, "currency": str(product.currency)},
                    new_value={"price": new_val, "currency": item.get("currency", "USD"),
                               "diff_amount": round(diff_amount, 2) if diff_amount else None,
                               "diff_percentage": round(diff_pct, 2) if diff_pct else None},
                    event_message=f"Price changed for {product.title}: {old_val} -> {new_val}",
                    detected_at=now,
                )
                session.add(event)
                product.current_price = new_price
                product.currency = item.get("currency", "USD")
                price_change_count += 1
                changed = True
                snapshot_needed = True

            # Stock change
            if old_stock != new_stock:
                stock_event = "stock_in" if new_stock == "in_stock" else "stock_out"
                event = Event(
                    competitor_id=competitor.id,
                    product_id=product.id,
                    event_type=stock_event,
                    old_value={"stock_status": old_stock},
                    new_value={"stock_status": new_stock},
                    event_message=f"Stock changed for {product.title}: {old_stock} -> {new_stock}",
                    detected_at=now,
                )
                session.add(event)
                product.stock_status = new_stock
                changed = True
                snapshot_needed = True

            # Title / image changes
            if product.title != item.get("title", "") or product.image_url != item.get("image_url"):
                product.title = item.get("title", product.title)
                product.image_url = item.get("image_url", product.image_url)
                product.normalized_title = normalize_title(product.title)
                snapshot_needed = True

            product.last_seen_at = now
            product.last_checked_at = now
            product.active = True
            product.consecutive_misses = 0

            if snapshot_needed:
                snapshot = ProductSnapshot(
                    product_id=product.id,
                    title=product.title,
                    price=product.current_price,
                    currency=product.currency,
                    stock_status=product.stock_status,
                    image_url=product.image_url,
                    checked_at=now,
                )
                session.add(snapshot)

    # Mark products not seen as missing
    for url, product in existing_products.items():
        if url not in scraped_urls and product.active:
            product.consecutive_misses = (product.consecutive_misses or 0) + 1
            product.last_checked_at = now
            if product.consecutive_misses >= CONSECUTIVE_MISS_THRESHOLD:
                product.active = False
                event = Event(
                    competitor_id=competitor.id,
                    product_id=product.id,
                    event_type="product_removed",
                    old_value={"url": product.url, "title": product.title},
                    new_value=None,
                    event_message=f"Product no longer seen: {product.title}",
                    detected_at=now,
                )
                session.add(event)

    return {"new_products": new_count, "price_changes": price_change_count}


def _prices_differ(old: Optional[Decimal], new: Optional[float]) -> bool:
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(float(old) - float(new)) > 0.001


def _get_price_event_type(old: Optional[Decimal], new: Optional[float]) -> str:
    if old is None or new is None:
        return "price_changed"
    if float(new) > float(old):
        return "price_increase"
    if float(new) < float(old):
        return "price_decrease"
    return "price_changed"
