import asyncio
import html
import json
import logging
import re
import ssl
from typing import Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

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
    if scrape_type == "salla_json":
        return await scrape_salla_json(competitor, max_pages=max_pages, user_agent=user_agent)
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

    timeout_seconds = selector_config.get("request_timeout_seconds", 30)
    connector = _aiohttp_connector(aiohttp)
    async with aiohttp.ClientSession(
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        connector=connector,
    ) as session:
        if selector_config.get("prefer_storefront_graphql"):
            all_products = await _scrape_shopify_storefront_graphql(session, base_url, selector_config)
            if all_products:
                return all_products

        collections = []
        if selector_config.get("discover_collections", True):
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


async def scrape_salla_json(competitor: dict, max_pages: int = MAX_PAGES_DEFAULT,
                            user_agent: str = "MarketMonitor/1.0") -> list[dict]:
    """Scrape Salla storefront category product APIs."""
    import aiohttp

    base_url = competitor.get("base_url", "").rstrip("/")
    selector_config = competitor.get("selector_config", {}) or {}
    listing_urls = competitor.get("listing_urls") or []
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/html;q=0.9",
        "Referer": listing_urls[0] if listing_urls else base_url,
    }
    timeout_seconds = selector_config.get("request_timeout_seconds", 30)
    per_page = min(int(selector_config.get("per_page") or 32), 32)
    source = selector_config.get("source") or "categories"
    currency = selector_config.get("currency") or "SAR"
    category_ids = _salla_category_ids(selector_config, listing_urls)
    headers["Cookie"] = f"s-curr={currency}"
    all_products = []
    seen = set()

    connector = _aiohttp_connector(aiohttp)
    async with aiohttp.ClientSession(
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        connector=connector,
    ) as session:
        if not category_ids and listing_urls:
            category_ids = await _discover_salla_category_ids(session, listing_urls, base_url)
        if not category_ids:
            logger.warning("No Salla category id found for %s", base_url)
            return []

        for category_id in category_ids:
            locale = _salla_locale(selector_config, listing_urls, base_url)
            next_url = _salla_products_api_url(base_url, locale, source, category_id, per_page, currency)
            category_name = None

            for _ in range(max_pages):
                try:
                    async with session.get(next_url) as resp:
                        if resp.status >= 400:
                            logger.warning("Salla JSON returned %s for %s", resp.status, next_url)
                            break
                        data = await resp.json(content_type=None)
                except Exception as e:
                    logger.warning("Could not fetch Salla JSON %s: %s", next_url, e)
                    break

                raw_products = data.get("data") or []
                if not raw_products:
                    break

                for raw in raw_products:
                    product = _extract_salla_product(raw, base_url, category_name)
                    category_name = category_name or product.get("category") if product else category_name
                    key = product.get("external_id") or product.get("url") if product else None
                    if product and key not in seen:
                        seen.add(key)
                        all_products.append(product)

                cursor = data.get("cursor") or {}
                next_url = cursor.get("next")
                if not next_url:
                    break
                next_url = _localize_salla_api_url(next_url, locale, currency)

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
    config = await _storefront_graphql_config(session, base_url, selector_config)
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


async def _storefront_graphql_config(session, base_url: str, selector_config: dict) -> Optional[dict]:
    configured = selector_config.get("storefront_graphql") or {}
    if configured.get("shop_domain") and configured.get("access_token"):
        return {
            "shop_domain": configured["shop_domain"],
            "access_token": configured["access_token"],
            "api_version": configured.get("api_version", "2025-07"),
            "collection_handles": configured.get("collection_handles") or selector_config.get("collection_handles") or [],
            "max_products": configured.get("max_products", 500),
        }

    if selector_config.get("auto_discover_storefront_graphql", True) is False:
        return None

    handles = selector_config.get("collection_handles") or await _discover_collection_handles(session, base_url)
    if not handles:
        return None

    assets_text = await _discover_storefront_assets_text(session, base_url)
    return _storefront_graphql_config_from_assets(base_url, selector_config, assets_text, handles)


def _storefront_graphql_config_from_assets(base_url: str, selector_config: dict, assets_text: str,
                                           collection_handles: list[str]) -> Optional[dict]:
    shop_domain = _regex_first(assets_text, r'["\']([a-z0-9][a-z0-9-]*\.myshopify\.com)["\']')
    if not shop_domain or "X-Shopify-Storefront-Access-Token" not in assets_text:
        return None

    access_token = _storefront_access_token_from_assets(assets_text)
    if not access_token:
        return None

    api_version = _api_version_from_assets(assets_text) or "2025-07"
    return {
        "shop_domain": shop_domain,
        "access_token": access_token,
        "api_version": api_version,
        "collection_handles": collection_handles,
        "max_products": selector_config.get("max_products", 500),
    }


def _storefront_access_token_from_assets(assets_text: str) -> Optional[str]:
    header_match = re.search(
        r'X-Shopify-Storefront-Access-Token["\']?\s*:\s*([A-Za-z_$][\w$]*|["\'][^"\']+["\'])',
        assets_text or "",
    )
    if header_match:
        raw_value = header_match.group(1).strip()
        if raw_value[0] in "\"'":
            return raw_value.strip("\"'")
        token_match = re.search(rf'\b{re.escape(raw_value)}\s*=\s*["\']([^"\']+)["\']', assets_text)
        if token_match:
            return token_match.group(1)

    token_match = re.search(r'["\']([a-f0-9]{32,})["\']', assets_text or "", re.IGNORECASE)
    return token_match.group(1) if token_match else None


def _api_version_from_assets(assets_text: str) -> Optional[str]:
    graphql_index = (assets_text or "").find("graphql.json")
    if graphql_index >= 0:
        nearby = assets_text[max(0, graphql_index - 300):graphql_index + 100]
        version = _regex_first(nearby, r'["\'](20\d{2}-\d{2})["\']')
        if version:
            return version
    return _regex_first(assets_text, r'api/([0-9]{4}-[0-9]{2})/graphql\.json')


async def _discover_collection_handles(session, base_url: str) -> list[str]:
    handles = await _collection_handles_from_sitemap_url(session, f"{base_url}/sitemap.xml", base_url, limit=50)
    if handles:
        return handles
    try:
        async with session.get(base_url, headers={"Accept": "text/html"}) as resp:
            if resp.status >= 400:
                return []
            body = await resp.text()
    except Exception:
        return []
    return _collection_handles_from_text(body)


async def _collection_handles_from_sitemap_url(session, sitemap_url: str, base_url: str, limit: int) -> list[str]:
    try:
        async with session.get(sitemap_url, headers={"Accept": "application/xml, text/xml, text/html"}) as resp:
            if resp.status >= 400:
                return []
            body = await resp.text()
    except Exception:
        return []

    handles = _collection_handles_from_text(body)
    if handles:
        return handles[:limit]

    nested_sitemaps = [
        loc for loc in _sitemap_locs(body)
        if loc.endswith(".xml") and urlparse(loc).netloc == urlparse(base_url).netloc
    ][:10]
    nested_handles = []
    for nested in nested_sitemaps:
        nested_handles.extend(await _collection_handles_from_sitemap_url(session, nested, base_url, limit - len(nested_handles)))
        if len(nested_handles) >= limit:
            break
    return list(dict.fromkeys(nested_handles))[:limit]


def _collection_handles_from_text(text: str) -> list[str]:
    ignored = {"all", "frontpage", "home-page", "new", "best-sellers"}
    handles = [
        html.unescape(match.group(1)).strip("/")
        for match in re.finditer(r"/collections/([A-Za-z0-9][A-Za-z0-9_-]*)", text or "")
    ]
    return [handle for handle in dict.fromkeys(handles) if handle not in ignored]


async def _discover_storefront_assets_text(session, base_url: str) -> str:
    try:
        async with session.get(base_url, headers={"Accept": "text/html"}) as resp:
            if resp.status >= 400:
                return ""
            body = await resp.text()
    except Exception:
        return ""

    asset_urls = _javascript_asset_urls(body, base_url)
    texts = []
    seen_urls = set()
    idx = 0
    while idx < len(asset_urls) and len(seen_urls) < 40:
        asset_url = asset_urls[idx]
        idx += 1
        if asset_url in seen_urls:
            continue
        seen_urls.add(asset_url)
        try:
            async with session.get(asset_url, headers={"Accept": "application/javascript, text/javascript, */*"}) as resp:
                if resp.status >= 400:
                    continue
                text = await resp.text()
        except Exception:
            continue
        texts.append(text)
        if "X-Shopify-Storefront-Access-Token" in text and "graphql.json" in text:
            break
        asset_urls.extend(_javascript_import_urls(text, asset_url))
    return "\n".join(texts)


def _javascript_asset_urls(html_body: str, base_url: str) -> list[str]:
    urls = []
    for match in re.finditer(r'(?:src|href)=["\']([^"\']+\.js(?:\?[^"\']*)?)["\']', html_body or "", re.IGNORECASE):
        urls.append(normalize_url(html.unescape(match.group(1)), base_url))
    return list(dict.fromkeys(urls))


def _javascript_import_urls(js_body: str, asset_url: str) -> list[str]:
    urls = []
    for match in re.finditer(r'(?:import\(|from\s*)["\']([^"\']+\.js(?:\?[^"\']*)?)["\']', js_body or ""):
        urls.append(normalize_url(html.unescape(match.group(1)), asset_url))
    return list(dict.fromkeys(urls))


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


def _extract_salla_product(raw: dict, base_url: str, category: Optional[str] = None) -> Optional[dict]:
    title = (raw.get("name") or raw.get("title") or "").strip()
    product_url = raw.get("url") or raw.get("custom_url")
    if not title or not product_url:
        return None

    raw_price = raw.get("price")
    price, currency = parse_price(str(raw_price) if raw_price is not None else None, raw.get("currency") or "SAR")
    image = raw.get("image") or {}
    image_url = image.get("url") if isinstance(image, dict) else image
    if not image_url:
        image_url = raw.get("original_image")

    stock_status = "unknown"
    if raw.get("is_out_of_stock") is True or raw.get("is_available") is False:
        stock_status = "out_of_stock"
    elif raw.get("is_available") is True or raw.get("status") == "sale":
        stock_status = "in_stock"

    raw_category = raw.get("category") or {}
    category_name = raw_category.get("name") if isinstance(raw_category, dict) else raw_category

    return {
        "title": html.unescape(title),
        "price": price,
        "currency": currency,
        "url": normalize_url(product_url, base_url),
        "image_url": normalize_url(image_url, base_url) if image_url else None,
        "stock_status": stock_status,
        "sku": raw.get("sku"),
        "external_id": str(raw.get("id")) if raw.get("id") is not None else None,
        "category": html.unescape(category or category_name or "Uncategorized"),
    }


def _salla_category_ids(selector_config: dict, listing_urls: list[str]) -> list[str]:
    configured = selector_config.get("category_ids") or selector_config.get("category_id") or []
    if isinstance(configured, (str, int)):
        configured = [str(configured)]
    ids = [str(value).strip() for value in configured if str(value).strip()]
    ids.extend(
        category_id
        for listing_url in listing_urls
        for category_id in [_salla_category_id_from_url(listing_url)]
        if category_id
    )
    return list(dict.fromkeys(ids))


async def _discover_salla_category_ids(session, listing_urls: list[str], base_url: str) -> list[str]:
    ids = []
    for listing_url in listing_urls:
        try:
            async with session.get(listing_url, headers={"Accept": "text/html"}) as resp:
                if resp.status >= 400:
                    continue
                body = await resp.text()
        except Exception:
            continue
        ids.extend(_salla_category_ids_from_html(body))
    return list(dict.fromkeys(ids))


def _salla_category_id_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url or "")
    match = re.search(r"/c(\d+)(?:/)?$", parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    for key in ("source_value", "source_value[]", "category_id"):
        values = query.get(key) or []
        if values and str(values[0]).isdigit():
            return str(values[0])
    return None


def _salla_category_ids_from_html(html_body: str) -> list[str]:
    ids = [
        match.group(1)
        for match in re.finditer(
            r"<salla-products-list\b[^>]*\bsource-value=[\"'](\d+)[\"']",
            html_body or "",
            re.IGNORECASE,
        )
    ]
    ids.extend(match.group(1) for match in re.finditer(r"/c(\d+)", html_body or ""))
    return list(dict.fromkeys(ids))


def _salla_locale(selector_config: dict, listing_urls: list[str], base_url: str) -> str:
    configured = selector_config.get("locale")
    if configured:
        return str(configured).strip("/")
    for url in [*listing_urls, base_url]:
        first_segment = (urlparse(url or "").path.strip("/").split("/") or [""])[0]
        if re.fullmatch(r"[a-z]{2}", first_segment or ""):
            return first_segment
    return "en"


def _salla_products_api_url(base_url: str, locale: str, source: str, category_id: str, per_page: int,
                            currency: Optional[str] = "SAR") -> str:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    query = urlencode({
        "source": source,
        "source_value[]": category_id,
        "per_page": per_page,
        "filterable": 1,
        "currency": currency,
    })
    return f"{origin}/{locale.strip('/')}/api/v1/products?{query}"


def _localize_salla_api_url(url: str, locale: str, currency: Optional[str] = "SAR") -> str:
    parsed = urlparse(url)
    localized_path = f"/{locale.strip('/')}/api/v1/products"
    if parsed.path == "/api/v1/products":
        parsed = parsed._replace(path=localized_path)
    if currency:
        params = parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key == "currency" for key, _ in params):
            params.append(("currency", currency))
        parsed = parsed._replace(query=urlencode(params))
    return urlunparse(parsed)


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
