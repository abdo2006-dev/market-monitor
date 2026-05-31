import asyncio
import html
import json
import logging
import re
import ssl
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
    listing_urls = competitor.get("listing_urls", [])
    if scrape_type == "shopify_json" or (scrape_type == "generic_selector" and not listing_urls):
        return await scrape_shopify_json(competitor, max_pages=max_pages, user_agent=user_agent)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed")
        return []

    selector_config = competitor.get("selector_config", {})
    base_url = competitor.get("base_url", "")
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
    """Scrape Shopify catalogs, preserving collection names as categories."""
    import aiohttp

    base_url = competitor.get("base_url", "").rstrip("/")
    selector_config = competitor.get("selector_config", {}) or {}
    listing_urls = competitor.get("listing_urls") or []
    headers = {"User-Agent": user_agent, "Accept": "application/json, text/html;q=0.9"}
    all_products = []
    seen_urls = set()

    connector = _aiohttp_connector(aiohttp)
    async with aiohttp.ClientSession(
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
        connector=connector,
    ) as session:
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

        if not all_products:
            all_products = await _scrape_custom_storefront_fallback(
                session,
                base_url,
                selector_config,
                max_pages=max_pages,
            )

    return all_products


def _aiohttp_connector(aiohttp):
    try:
        import certifi
    except ImportError:
        return None
    return aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))


async def _scrape_custom_storefront_fallback(session, base_url: str, selector_config: dict,
                                             max_pages: int = MAX_PAGES_DEFAULT) -> list[dict]:
    """Fallback for Shopify-backed custom storefronts that hide products.json."""
    products = await _scrape_shopify_storefront_graphql(session, base_url, selector_config)
    if products:
        return products
    return await _scrape_sitemap_product_pages(session, base_url, selector_config, max_pages=max_pages)


async def _scrape_shopify_storefront_graphql(session, base_url: str, selector_config: dict) -> list[dict]:
    config = _storefront_graphql_config(base_url, selector_config)
    if not config:
        return []

    endpoint = f"https://{config['shop_domain']}/api/{config['api_version']}/graphql.json"
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Storefront-Access-Token": config["access_token"],
    }
    products = []
    seen = set()
    max_products = int(config.get("max_products", 500))

    for handle in config["collection_handles"]:
        remaining = max_products
        after = None
        category = _title_from_handle(handle)
        while remaining > 0:
            first = min(remaining, 250)
            payload = {
                "query": _STOREFRONT_COLLECTION_QUERY,
                "variables": {"handle": handle, "first": first, "after": after},
            }
            try:
                async with session.post(endpoint, json=payload, headers=headers) as resp:
                    if resp.status >= 400:
                        logger.warning("Storefront GraphQL returned %s for %s", resp.status, base_url)
                        return products
                    data = await resp.json(content_type=None)
            except Exception as e:
                logger.warning("Could not fetch Storefront GraphQL for %s: %s", base_url, e)
                return products

            if data.get("errors"):
                logger.warning("Storefront GraphQL errors for %s: %s", base_url, data["errors"])
                return products

            collection = (data.get("data") or {}).get("collection") or {}
            category = collection.get("title") or category
            product_edges = ((collection.get("products") or {}).get("edges")) or []
            page_info = (collection.get("products") or {}).get("pageInfo") or {}
            if not product_edges:
                break

            for edge in product_edges:
                product = _extract_storefront_product(edge.get("node") or {}, base_url, category)
                key = product.get("external_id") or product.get("url") if product else None
                if product and key not in seen:
                    seen.add(key)
                    products.append(product)

            remaining -= len(product_edges)
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            if not after:
                break

    return products


def _storefront_graphql_config(base_url: str, selector_config: dict) -> Optional[dict]:
    configured = selector_config.get("storefront_graphql") or {}
    if configured.get("shop_domain") and configured.get("access_token"):
        return {
            "shop_domain": configured["shop_domain"],
            "access_token": configured["access_token"],
            "api_version": configured.get("api_version", "2025-07"),
            "collection_handles": configured.get("collection_handles") or selector_config.get("collection_handles") or [],
            "max_products": configured.get("max_products", 500),
        }

    host = urlparse(base_url).netloc.lower().removeprefix("www.")
    if host == "bloxcrews.com":
        return {
            "shop_domain": "05ce1a-4c.myshopify.com",
            "access_token": "b4945c4d79c27891c0ab9c3d95daf9d8",
            "api_version": "2025-07",
            "collection_handles": ["steal-a-brainrot", "adopt-me"],
            "max_products": 500,
        }
    return None


_STOREFRONT_PRODUCT_FIELDS = """
id
title
handle
vendor
productType
images(first: 1) { edges { node { url altText } } }
variants(first: 10) {
  edges {
    node {
      id
      sku
      availableForSale
      price { amount currencyCode }
      compareAtPrice { amount currencyCode }
    }
  }
}
"""


_STOREFRONT_COLLECTION_QUERY = f"""
query GetCollectionProducts($handle: String!, $first: Int!, $after: String) {{
  collection(handle: $handle) {{
    title
    products(first: $first, after: $after) {{
      edges {{ cursor node {{ {_STOREFRONT_PRODUCT_FIELDS} }} }}
      pageInfo {{ hasNextPage endCursor }}
    }}
  }}
}}
"""


def _extract_storefront_product(raw: dict, base_url: str, category: Optional[str]) -> Optional[dict]:
    title = (raw.get("title") or "").strip()
    handle = raw.get("handle")
    if not title or not handle:
        return None

    variants = ((raw.get("variants") or {}).get("edges")) or []
    variant_node = None
    for edge in variants:
        node = edge.get("node") or {}
        if node.get("availableForSale"):
            variant_node = node
            break
    if not variant_node and variants:
        variant_node = (variants[0].get("node") or {})
    variant_node = variant_node or {}

    price_data = variant_node.get("price") or {}
    price, currency = parse_price(price_data.get("amount"), price_data.get("currencyCode") or "USD")
    image_edges = ((raw.get("images") or {}).get("edges")) or []
    image_url = None
    if image_edges:
        image_url = (image_edges[0].get("node") or {}).get("url")

    product_id = _shopify_gid_id(raw.get("id"))
    variant_id = _shopify_gid_id(variant_node.get("id"))
    return {
        "title": title,
        "price": price,
        "currency": currency,
        "url": f"{base_url}/product/{handle}",
        "image_url": image_url,
        "stock_status": "in_stock" if variant_node.get("availableForSale") else "out_of_stock",
        "sku": variant_node.get("sku"),
        "external_id": f"{product_id}:{variant_id}" if product_id and variant_id else product_id or variant_id,
        "category": category or raw.get("productType") or raw.get("vendor") or "Uncategorized",
    }


async def _scrape_sitemap_product_pages(session, base_url: str, selector_config: dict,
                                        max_pages: int = MAX_PAGES_DEFAULT) -> list[dict]:
    max_products = int(selector_config.get("max_sitemap_products") or max(max_pages * 250, 250))
    product_urls = await _product_urls_from_sitemap_url(session, f"{base_url}/sitemap.xml", base_url, max_products)
    if not product_urls:
        return []

    semaphore = asyncio.Semaphore(int(selector_config.get("sitemap_concurrency") or 10))

    async def fetch_product(url: str) -> Optional[dict]:
        async with semaphore:
            try:
                async with session.get(url, headers={"Accept": "text/html"}) as resp:
                    if resp.status >= 400:
                        return None
                    body = await resp.text()
            except Exception as e:
                logger.debug("Could not fetch product page %s: %s", url, e)
                return None
            return _extract_product_from_storefront_html(body, url, base_url)

    parsed = await asyncio.gather(*(fetch_product(url) for url in product_urls[:max_products]))
    products = []
    seen = set()
    for product in parsed:
        key = product.get("external_id") or product.get("url") if product else None
        if product and key not in seen:
            seen.add(key)
            products.append(product)
    return products


async def _product_urls_from_sitemap_url(session, sitemap_url: str, base_url: str, limit: int) -> list[str]:
    try:
        async with session.get(sitemap_url, headers={"Accept": "application/xml, text/xml, text/html"}) as resp:
            if resp.status >= 400:
                return []
            body = await resp.text()
    except Exception as e:
        logger.debug("Could not fetch sitemap %s: %s", sitemap_url, e)
        return []

    urls = _product_urls_from_sitemap(body, base_url)
    if urls:
        return urls[:limit]

    nested_sitemaps = [
        loc for loc in _sitemap_locs(body)
        if loc.endswith(".xml") and urlparse(loc).netloc == urlparse(base_url).netloc
    ][:10]
    nested_urls = []
    for nested in nested_sitemaps:
        nested_urls.extend(await _product_urls_from_sitemap_url(session, nested, base_url, limit - len(nested_urls)))
        if len(nested_urls) >= limit:
            break
    return nested_urls[:limit]


def _product_urls_from_sitemap(sitemap_body: str, base_url: str) -> list[str]:
    urls = []
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    for loc in _sitemap_locs(sitemap_body):
        parsed = urlparse(loc)
        host = parsed.netloc.lower().removeprefix("www.")
        if host != base_host:
            continue
        if re.search(r"/products?/", parsed.path):
            urls.append(loc)
    return list(dict.fromkeys(urls))


def _sitemap_locs(sitemap_body: str) -> list[str]:
    return [
        html.unescape(match.group(1).strip())
        for match in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>", sitemap_body or "", re.IGNORECASE)
    ]


def _extract_product_from_storefront_html(html_body: str, page_url: str, base_url: str) -> Optional[dict]:
    product_json = _json_ld_product(html_body)
    fallback_title = _regex_first(html_body, r'title:"([^"]+)"')
    title = ((product_json or {}).get("name") or fallback_title or "").strip()
    if not title:
        return None

    offers = (product_json or {}).get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    raw_price = offers.get("price") or _regex_first(html_body, r'price:\$R\[\d+\]=\{amount:"([^"]+)"')
    raw_currency = offers.get("priceCurrency") or _regex_first(html_body, r'currencyCode:"([A-Z]{3})"') or "USD"
    price, currency = parse_price(raw_price, raw_currency)

    image = (product_json or {}).get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    image_url = image or _regex_first(html_body, r'url:"(https?://cdn\.shopify\.com/[^"]+)"')

    product_id = _regex_first(html_body, r"gid://shopify/Product/(\d+)")
    variant_id = _regex_first(html_body, r"gid://shopify/ProductVariant/(\d+)")
    sku = (product_json or {}).get("sku")
    if not variant_id:
        variant_id = _shopify_gid_id(sku)

    availability = str(offers.get("availability") or "")
    available_flag = _regex_first(html_body, r"availableForSale:(!0|!1|true|false)")
    if "outofstock" in availability.lower() or available_flag in ("!1", "false"):
        stock_status = "out_of_stock"
    elif "instock" in availability.lower() or available_flag in ("!0", "true"):
        stock_status = "in_stock"
    else:
        stock_status = "unknown"

    category = _json_ld_brand(product_json) or _regex_first(html_body, r'productType:"([^"]*)"') or "Uncategorized"

    return {
        "title": html.unescape(title),
        "price": price,
        "currency": currency,
        "url": normalize_url(page_url, base_url),
        "image_url": image_url,
        "stock_status": stock_status,
        "sku": sku,
        "external_id": f"{product_id}:{variant_id}" if product_id and variant_id else product_id or variant_id,
        "category": html.unescape(category) if category else "Uncategorized",
    }


def _json_ld_product(html_body: str) -> Optional[dict]:
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html_body or "",
        re.IGNORECASE | re.DOTALL,
    ):
        raw = html.unescape(match.group(1).strip())
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _json_ld_items(data):
            item_type = item.get("@type")
            if item_type == "Product" or (isinstance(item_type, list) and "Product" in item_type):
                return item
    return None


def _json_ld_items(data) -> list[dict]:
    if isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            return [item for item in graph if isinstance(item, dict)]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _json_ld_brand(product_json: Optional[dict]) -> Optional[str]:
    brand = (product_json or {}).get("brand")
    if isinstance(brand, dict):
        return brand.get("name")
    if isinstance(brand, str):
        return brand
    return None


def _regex_first(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text or "", re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(1)) if match else None


def _shopify_gid_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    match = re.search(r"(\d+)$", str(value))
    return match.group(1) if match else str(value)


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
