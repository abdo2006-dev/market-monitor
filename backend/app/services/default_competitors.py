DEFAULT_COMPETITORS = [
    ("BloxCrew", "https://bloxcrews.com/"),
    ("Bloxy Store", "https://bloxystores.com/"),
    ("PetPatch.GG", "https://petpatch.gg/"),
    ("Shopbloxs", "https://shopbloxs.com/"),
    ("Luger.GG", "https://luger.gg/"),
    ("BuyBlox", "https://buyblox.gg/"),
]


def default_competitor_payloads() -> list[dict]:
    return [
        {
            "name": name,
            "base_url": url,
            "category": "Roblox marketplace",
            "active": True,
            "scan_frequency_minutes": 60,
            "scrape_type": "shopify_json",
            "listing_urls": [],
            "selector_config": {"discover_collections": True, "include_all_products": True},
            "notes": "Starter Shopify competitor. Collections are detected as categories.",
        }
        for name, url in DEFAULT_COMPETITORS
    ]
