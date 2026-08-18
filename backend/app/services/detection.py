import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models import Product, ProductSnapshot, Event, Competitor
from app.domain.product_identity import prepare_product_observations, product_identity_key
from app.utils.text_normalizer import normalize_title

logger = logging.getLogger(__name__)

CONSECUTIVE_MISS_THRESHOLD = 3


async def detect_changes(
    session: AsyncSession,
    competitor: Competitor,
    scraped_products: list[dict],
    *,
    scrape_run_id: int | None = None,
    observed_at: datetime | None = None,
    allow_absence: bool = True,
) -> dict:
    """
    Match scraped products against stored products, create/update records,
    generate events for changes. Returns summary counts.
    """
    now = datetime.now(timezone.utc)
    catalog_observed_at = observed_at or now
    new_count = 0
    price_change_count = 0

    admissible = [item for item in scraped_products if item.get("url")]
    observations, identity_conflicts = prepare_product_observations(
        admissible, competitor.scrape_type
    )
    for conflict in identity_conflicts:
        logger.warning(
            "Collapsed duplicate observations for competitor_id=%s: %s",
            competitor.id,
            conflict,
            extra={
                "competitor_id": competitor.id,
                "operation": "deduplicate_observations",
                "product_identity": conflict,
            },
        )

    # This read starts the protected read/decide/write reconciliation region.
    # The caller must hold the competitor's transaction-scoped advisory lock.
    result = await session.execute(
        select(Product).where(Product.competitor_id == competitor.id)
    )
    existing_products = result.scalars().all()
    existing_by_url, existing_by_identity_key = _index_existing_products(existing_products)
    seen_product_ids = set()

    for item in observations:
        item_observed_at = item.get("observed_at") or catalog_observed_at
        url = item["url"]
        canonical_url = item["canonical_url"]
        identity_key = item.get("identity_key")
        norm_title = normalize_title(item.get("title", ""))

        # Match by stable identifiers only. Product names are useful for search,
        # but are not reliable IDs because stores often reuse short item names.
        product = _match_existing_product(
            canonical_url,
            identity_key,
            existing_by_url,
            existing_by_identity_key,
        )

        if not product:
            # New product
            product = Product(
                competitor_id=competitor.id,
                external_id=item.get("external_id"),
                title=item.get("title", ""),
                normalized_title=norm_title,
                category=item.get("category"),
                url=url,
                canonical_url=canonical_url,
                identity_key=identity_key,
                image_url=item.get("image_url"),
                current_price=item.get("price"),
                currency=item.get("currency", "USD"),
                stock_status=item.get("stock_status", "unknown"),
                sku=item.get("sku"),
                first_seen_at=now,
                last_seen_at=now,
                last_checked_at=now,
                last_observed_at=item_observed_at,
                last_observed_run_id=scrape_run_id,
                active=True,
                consecutive_misses=0,
            )
            session.add(product)
            await session.flush()  # Get product.id

            snapshot = ProductSnapshot(
                product_id=product.id,
                title=product.title,
                category=product.category,
                price=product.current_price,
                currency=product.currency,
                stock_status=product.stock_status,
                image_url=product.image_url,
                checked_at=now,
                observed_at=item_observed_at,
                scrape_run_id=scrape_run_id,
            )
            session.add(snapshot)

            event = Event(
                competitor_id=competitor.id,
                product_id=product.id,
                event_type="new_product",
                old_value=None,
                new_value={
                    "title": product.title,
                    "category": product.category,
                    "price": float(product.current_price) if product.current_price else None,
                    "currency": product.currency,
                    "stock_status": product.stock_status,
                    "url": product.url,
                },
                event_message=f"New product found: {product.title}",
                detected_at=now,
                scrape_run_id=scrape_run_id,
            )
            session.add(event)
            new_count += 1
            existing_by_url[canonical_url] = product
            if identity_key:
                existing_by_identity_key[identity_key] = product
            seen_product_ids.add(product.id)
        else:
            # Existing product - check for changes
            changed = False
            snapshot_needed = False
            seen_product_ids.add(product.id)

            # External observation time, not request or commit order, owns current
            # product freshness. Equal timestamps are deterministically ordered by
            # ScrapeRun id; legacy observations without a run id do not displace a
            # V2 observation at the same instant.
            if not _is_newer_observation(
                item_observed_at,
                scrape_run_id,
                product.last_observed_at,
                product.last_observed_run_id,
            ):
                continue

            old_price = product.current_price
            new_price = item.get("price")
            old_stock = product.stock_status
            new_stock = item.get("stock_status", "unknown")
            old_category = product.category
            new_category = item.get("category") or old_category

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
                    old_value={"price": old_val, "currency": str(product.currency), "category": product.category},
                    new_value={"price": new_val, "currency": item.get("currency", "USD"),
                               "category": new_category,
                               "diff_amount": round(diff_amount, 2) if diff_amount else None,
                               "diff_percentage": round(diff_pct, 2) if diff_pct else None},
                    event_message=f"Price changed for {product.title}: {old_val} -> {new_val}",
                    detected_at=now,
                    scrape_run_id=scrape_run_id,
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
                    old_value={"stock_status": old_stock, "category": product.category},
                    new_value={"stock_status": new_stock, "category": new_category},
                    event_message=f"Stock changed for {product.title}: {old_stock} -> {new_stock}",
                    detected_at=now,
                    scrape_run_id=scrape_run_id,
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

            if product.url != url:
                product.url = url
                snapshot_needed = True

            if product.canonical_url != canonical_url:
                existing_by_url.pop(product.canonical_url, None)
                product.canonical_url = canonical_url
                existing_by_url[canonical_url] = product

            if product.external_id != item.get("external_id"):
                product.external_id = item.get("external_id")

            if product.identity_key != identity_key:
                if product.identity_key:
                    existing_by_identity_key.pop(product.identity_key, None)
                product.identity_key = identity_key
                if identity_key:
                    existing_by_identity_key[identity_key] = product

            if new_category != old_category:
                product.category = new_category
                snapshot_needed = True

            product.last_seen_at = now
            product.last_checked_at = now
            product.last_observed_at = item_observed_at
            product.last_observed_run_id = scrape_run_id
            product.active = True
            product.consecutive_misses = 0

            if snapshot_needed:
                snapshot = ProductSnapshot(
                    product_id=product.id,
                    title=product.title,
                    category=product.category,
                    price=product.current_price,
                    currency=product.currency,
                    stock_status=product.stock_status,
                    image_url=product.image_url,
                    checked_at=now,
                    observed_at=item_observed_at,
                    scrape_run_id=scrape_run_id,
                )
                session.add(snapshot)

    # Absence is evidence only for a complete catalog observation. Partial,
    # suspicious-empty, and failed acquisitions call with allow_absence=False.
    for product in existing_products:
        if (
            allow_absence
            and product.id not in seen_product_ids
            and product.active
            and _is_newer_observation(
                catalog_observed_at,
                scrape_run_id,
                product.last_observed_at,
                product.last_observed_run_id,
            )
        ):
            product.consecutive_misses = (product.consecutive_misses or 0) + 1
            product.last_checked_at = now
            if product.consecutive_misses >= CONSECUTIVE_MISS_THRESHOLD:
                product.active = False
                event = Event(
                    competitor_id=competitor.id,
                    product_id=product.id,
                    event_type="product_removed",
                    old_value={"url": product.url, "title": product.title, "category": product.category},
                    new_value=None,
                    event_message=f"Product no longer seen: {product.title}",
                    detected_at=now,
                    scrape_run_id=scrape_run_id,
                )
                session.add(event)

    return {"new_products": new_count, "price_changes": price_change_count}


def _is_newer_observation(
    candidate_at: datetime,
    candidate_run_id: int | None,
    current_at: datetime | None,
    current_run_id: int | None,
) -> bool:
    if current_at is None or candidate_at > current_at:
        return True
    if candidate_at < current_at:
        return False
    if candidate_run_id is None:
        return current_run_id is None
    if current_run_id is None:
        return True
    return candidate_run_id > current_run_id


def _prices_differ(old: Optional[Decimal], new: Optional[float]) -> bool:
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(float(old) - float(new)) > 0.001


def _index_existing_products(products: list[Product]) -> tuple[dict[str, Product], dict[str, Product]]:
    by_url = {getattr(p, "canonical_url", p.url): p for p in products}
    by_identity_key = {
        (getattr(p, "identity_key", None) or product_identity_key(p.external_id)): p
        for p in products
        if getattr(p, "identity_key", None) or product_identity_key(p.external_id)
    }
    return by_url, by_identity_key


def _match_existing_product(
    canonical_url: str,
    identity_key: Optional[str],
    existing_by_url: dict[str, Product],
    existing_by_identity_key: dict[str, Product],
) -> Optional[Product]:
    product = existing_by_url.get(canonical_url)
    if product:
        return product
    if identity_key:
        return existing_by_identity_key.get(identity_key)
    return None


def _get_price_event_type(old: Optional[Decimal], new: Optional[float]) -> str:
    if old is None or new is None:
        return "price_changed"
    if float(new) > float(old):
        return "price_increase"
    if float(new) < float(old):
        return "price_decrease"
    return "price_changed"
