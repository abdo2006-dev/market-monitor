"""Canonical, non-sensitive registry for live competitor acceptance.

This is a diagnostic registry, not a second production database. It contains
only the public storefront coordinates and adapter hints needed by the
read-only live coverage runner. Product selectors and credentials continue to
belong to normal operator configuration; no token or private value is allowed
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


SHOPIFY_STRATEGIES = (
    "shopify_products_json_aiohttp",
    "shopify_products_json_httpx",
    "shopify_storefront_graphql",
    "shopify_sitemap_product_pages",
)


@dataclass(frozen=True)
class CoverageCompetitor:
    name: str
    base_url: str
    scrape_type: str
    expected_strategies: tuple[str, ...]
    expected_non_empty: bool = True
    listing_urls: tuple[str, ...] = ()
    selector_config: dict = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    previous_product_count: int | None = None
    caveat: str | None = None

    def acquisition_payload(self) -> dict:
        return {
            "id": 0,
            "name": self.name,
            "base_url": self.base_url,
            "scrape_type": self.scrape_type,
            "listing_urls": list(self.listing_urls),
            "selector_config": dict(self.selector_config),
        }


def _shopify(
    name: str,
    base_url: str,
    *,
    expected_strategies: tuple[str, ...] = SHOPIFY_STRATEGIES,
    caveat: str | None = None,
) -> CoverageCompetitor:
    return CoverageCompetitor(
        name=name,
        base_url=base_url,
        scrape_type="shopify_json",
        expected_strategies=expected_strategies,
        selector_config={
            "discover_collections": True,
            "include_all_products": True,
            "prefer_all_products_first": True,
            "request_timeout_seconds": 30,
        },
        caveat=caveat,
    )


LIVE_COMPETITORS: tuple[CoverageCompetitor, ...] = (
    _shopify("Zyron", "https://zyron.gg/"),
    _shopify("Bloxloot", "https://bloxloot.gg/"),
    CoverageCompetitor(
        name="TubbysTubby",
        base_url="https://tubbystubby.com/",
        scrape_type="salla_json",
        expected_strategies=("salla_json",),
        listing_urls=("https://tubbystubby.com/en/-/c306488438/?lang=en",),
        selector_config={
            "category_ids": ["306488438"],
            "currency": "USD",
            "locale": "en",
            "request_timeout_seconds": 30,
        },
        aliases=("https://tubbyshtubby.com/",),
        previous_product_count=145,
        caveat=(
            "The historical Salla hostname was tubbyshtubby.com; the owner-provided "
            "canonical hostname is tested and any parked/changed storefront is reported."
        ),
    ),
    _shopify("Bloxshop", "https://bloxshop.org/"),
    _shopify("BloxyBarn", "https://www.bloxybarn.gg/"),
    _shopify("MM2Cheap", "https://mm2.cheap/"),
    _shopify(
        "Shopbloxs",
        "https://shopbloxs.com/",
        expected_strategies=("shopify_storefront_graphql",),
        caveat="Custom frontend; public Storefront GraphQL is expected when products.json is unavailable.",
    ),
    _shopify("Luger.GG", "https://luger.gg/"),
    _shopify("BuyBlox", "https://buyblox.gg/"),
    _shopify("PetPatch.GG", "https://petpatch.gg/"),
    _shopify(
        "BloxCrew",
        "https://bloxcrews.com/",
        expected_strategies=(
            "shopify_storefront_graphql",
            "shopify_sitemap_product_pages",
        ),
        caveat="Custom storefront fallback may be required when products.json is unavailable.",
    ),
    _shopify("Bloxy Store", "https://bloxystores.com/"),
)


def get_live_competitors(names: set[str] | None = None) -> list[CoverageCompetitor]:
    if not names:
        return list(LIVE_COMPETITORS)
    normalized = {name.casefold() for name in names}
    return [item for item in LIVE_COMPETITORS if item.name.casefold() in normalized]
