import asyncio
import logging
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
                            card, selector_config, base_url, default_currency
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


async def _extract_product(card, selector_config: dict, base_url: str, default_currency: str) -> Optional[dict]:
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
        }
    except Exception as e:
        logger.debug(f"Failed extracting product: {e}")
        return None
