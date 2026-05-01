import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from app.utils.price_parser import parse_price
from app.utils.text_normalizer import normalize_url

logger = logging.getLogger(__name__)

MAX_PAGES_DEFAULT = 5
PAGE_DELAY_DEFAULT = 2.0
TIMEOUT_DEFAULT = 30000  # ms


def _detect_stock(text: Optional[str]) -> str:
    if not text:
        return "unknown"
    lower = text.lower()
    if any(w in lower for w in ["out of stock", "unavailable", "sold out", "out-of-stock"]):
        return "out_of_stock"
    if any(w in lower for w in ["in stock", "available", "add to cart", "buy now", "in-stock"]):
        return "in_stock"
    return "unknown"


async def scrape_competitor(competitor: dict, max_pages: int = MAX_PAGES_DEFAULT,
                             page_delay: float = PAGE_DELAY_DEFAULT,
                             headless: bool = True,
                             user_agent: str = "MarketMonitor/1.0") -> list[dict]:
    """
    Generic Playwright scraper.
    competitor dict must have: base_url, listing_urls, selector_config
    Returns list of product dicts.
    """
    scrape_type = competitor.get("scrape_type", "generic_selector")
    if scrape_type == "shopify_json":
        return await scrape_shopify_json(competitor, max_pages=max_pages, user_agent=user_agent)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed")
        return []

    selector_config = competitor.get("selector_config", {})
    base_url = competitor.get("base_url", "")
    listing_urls = competitor.get("listing_urls", [])
    default_currency = "USD"

    all_products = []
    seen_urls = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUT_DEFAULT)

        for listing_url in listing_urls:
            current_url = listing_url
            page_num = 0

            while current_url and page_num < max_pages:
                try:
                    logger.info(f"Scraping: {current_url}")
                    await page.goto(current_url, wait_until="domcontentloaded")
                    await asyncio.sleep(page_delay)

                    product_card_sel = selector_config.get("product_card", "")
                    if not product_card_sel:
                        logger.warning("No product_card selector configured")
                        break

                    cards = await page.query_selector_all(product_card_sel)
                    logger.info(f"Found {len(cards)} product cards on {current_url}")

                    for card in cards:
                        product = await _extract_product(
                            card, selector_config, base_url, default_currency,
                            category=_category_from_url(current_url),
                        )
                        if product and product["url"] not in seen_urls:
                            seen_urls.add(product["url"])
                            all_products.append(product)

                    # Pagination
                    pagination_sel = selector_config.get("pagination_next", "")
                    next_url = None
                    if pagination_sel:
                        next_el = await page.query_selector(pagination_sel)
                        if next_el:
                            href = await next_el.get_attribute("href")
                            if href:
                                next_url = normalize_url(href, base_url)

                    current_url = next_url
                    page_num += 1

                    if current_url:
                        await asyncio.sleep(page_delay)

                except Exception as e:
                    logger.error(f"Error scraping {current_url}: {e}")
                    break

        await browser.close()

    return all_products


async def scrape_shopify_json(competitor: dict, max_pages: int = MAX_PAGES_DEFAULT,
                              user_agent: str = "MarketMonitor/1.0") -> list[dict]:
    """Scrape public Shopify JSON endpoints, preserving collection names as categories."""
    import aiohttp

    base_url = competitor.get("base_url", "").rstrip("/")
    selector_config = competitor.get("selector_config", {}) or {}
    listing_urls = competitor.get("listing_urls") or []
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    all_products = []
    seen_urls = set()

    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:
        collections = await _shopify_collections(session, base_url)
        targets = _shopify_targets(base_url, listing_urls, collections, selector_config)
        if not targets:
            targets = [{"url": f"{base_url}/products.json", "category": None}]

        for target in targets:
            for page_num in range(1, max_pages + 1):
                url = f"{target['url']}{'&' if '?' in target['url'] else '?'}limit=250&page={page_num}"
                try:
                    async with session.get(url) as resp:
                        if resp.status >= 400:
                            logger.warning("Shopify JSON returned %s for %s", resp.status, url)
                            break
                        data = await resp.json(content_type=None)
                except Exception as e:
                    logger.warning("Could not fetch Shopify JSON %s: %s", url, e)
                    break

                products = data.get("products") or []
                if not products:
                    break

                for raw in products:
                    product = _extract_shopify_product(raw, base_url, target.get("category"))
                    if product and product["url"] not in seen_urls:
                        seen_urls.add(product["url"])
                        all_products.append(product)

    return all_products


async def _shopify_collections(session, base_url: str) -> list[dict]:
    try:
        async with session.get(f"{base_url}/collections.json") as resp:
            if resp.status >= 400:
                return []
            data = await resp.json(content_type=None)
            return data.get("collections") or []
    except Exception as e:
        logger.warning("Could not fetch Shopify collections for %s: %s", base_url, e)
        return []


def _shopify_targets(base_url: str, listing_urls: list[str], collections: list[dict], selector_config: dict) -> list[dict]:
    allowed_handles = set(selector_config.get("collection_handles") or [])
    discover = selector_config.get("discover_collections", True)
    include_all_products = selector_config.get("include_all_products", True)
    targets = []

    for listing_url in listing_urls:
        category = _category_from_url(listing_url)
        if "/collections/" in listing_url:
            clean = listing_url.rstrip("/")
            targets.append({"url": f"{clean}/products.json", "category": category})
        elif listing_url.endswith("/products.json"):
            targets.append({"url": listing_url, "category": category})

    if discover:
        for collection in collections:
            handle = collection.get("handle")
            if not handle:
                continue
            if allowed_handles and handle not in allowed_handles:
                continue
            if collection.get("products_count") == 0:
                continue
            targets.append({
                "url": f"{base_url}/collections/{handle}/products.json",
                "category": collection.get("title") or _title_from_handle(handle),
            })

    if include_all_products:
        targets.append({"url": f"{base_url}/products.json", "category": None})

    unique = {}
    for target in targets:
        unique[target["url"]] = target
    return list(unique.values())


def _extract_shopify_product(raw: dict, base_url: str, category: Optional[str]) -> Optional[dict]:
    variants = raw.get("variants") or []
    variant = variants[0] if variants else {}
    handle = raw.get("handle")
    if not handle:
        return None
    price, currency = parse_price(variant.get("price"), "USD")
    images = raw.get("images") or []
    image_url = images[0].get("src") if images else None
    product_id = raw.get("id")
    variant_id = variant.get("id")
    title = (raw.get("title") or "").strip()
    if not title:
        return None

    return {
        "title": title,
        "price": price,
        "currency": currency,
        "url": f"{base_url}/products/{handle}",
        "image_url": image_url,
        "stock_status": "in_stock" if variant.get("available", True) else "out_of_stock",
        "sku": variant.get("sku"),
        "external_id": f"{product_id}:{variant_id}" if variant_id else str(product_id),
        "category": category or raw.get("vendor") or raw.get("product_type") or "Uncategorized",
    }


def _category_from_url(url: str) -> Optional[str]:
    match = re.search(r"/collections/([^/?#]+)", url or "")
    if match:
        return _title_from_handle(match.group(1))
    return None


def _title_from_handle(handle: str) -> str:
    return re.sub(r"\s+", " ", handle.replace("-", " ")).strip().title()


async def _extract_product(card, selector_config: dict, base_url: str, default_currency: str,
                           category: Optional[str] = None) -> Optional[dict]:
    """Extract a single product from a card element."""
    try:
        # Title
        title_sel = selector_config.get("title", "")
        title = ""
        if title_sel:
            el = await card.query_selector(title_sel)
            if el:
                title = (await el.inner_text()).strip()
        if not title:
            title = (await card.inner_text()).strip()[:200]

        # Price
        price_sel = selector_config.get("price", "")
        raw_price = ""
        if price_sel:
            el = await card.query_selector(price_sel)
            if el:
                raw_price = (await el.inner_text()).strip()
        price, currency = parse_price(raw_price, default_currency)

        # URL
        url_sel = selector_config.get("url", "a")
        product_url = ""
        if url_sel:
            el = await card.query_selector(url_sel)
            if el:
                href = await el.get_attribute("href")
                if href:
                    product_url = normalize_url(href, base_url)
        if not product_url:
            return None

        # Image
        image_sel = selector_config.get("image", "img")
        image_url = None
        if image_sel:
            el = await card.query_selector(image_sel)
            if el:
                src = await el.get_attribute("src") or await el.get_attribute("data-src")
                if src:
                    image_url = normalize_url(src, base_url)

        # Stock
        stock_sel = selector_config.get("stock", "")
        stock_text = ""
        if stock_sel:
            el = await card.query_selector(stock_sel)
            if el:
                stock_text = (await el.inner_text()).strip()
        stock_status = _detect_stock(stock_text)

        if not title:
            return None

        return {
            "title": title,
            "price": price,
            "currency": currency,
            "url": product_url,
            "image_url": image_url,
            "stock_status": stock_status,
            "sku": None,
            "external_id": None,
            "category": category,
        }
    except Exception as e:
        logger.debug(f"Failed extracting product: {e}")
        return None
