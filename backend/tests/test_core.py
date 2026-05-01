"""
Tests for Market Monitor core utilities and services.
Run with: pytest tests/ -v
"""
import pytest
from app.utils.price_parser import parse_price
from app.utils.text_normalizer import normalize_title, normalize_url


# ── Price Parser Tests ─────────────────────────────────────────────────────────

class TestPriceParser:
    def test_usd_symbol(self):
        price, currency = parse_price("$39.99")
        assert price == 39.99
        assert currency == "USD"

    def test_eur_symbol(self):
        price, currency = parse_price("€29.50")
        assert price == 29.50
        assert currency == "EUR"

    def test_gbp_symbol(self):
        price, currency = parse_price("£15.00")
        assert price == 15.00
        assert currency == "GBP"

    def test_usd_code(self):
        price, currency = parse_price("USD 39.99")
        assert price == 39.99
        assert currency == "USD"

    def test_european_comma_decimal(self):
        price, currency = parse_price("39,99", "EUR")
        assert price == 39.99

    def test_european_full_format(self):
        price, currency = parse_price("1.299,00", "EUR")
        assert price == 1299.00

    def test_us_thousands(self):
        price, currency = parse_price("1,299.00")
        assert price == 1299.00

    def test_none_input(self):
        price, currency = parse_price(None)
        assert price is None

    def test_empty_string(self):
        price, currency = parse_price("")
        assert price is None

    def test_whitespace_and_text(self):
        price, currency = parse_price("  Price: $49.95  ")
        assert price == 49.95

    def test_free_product(self):
        price, currency = parse_price("$0.00")
        assert price == 0.00

    def test_large_price(self):
        price, currency = parse_price("$1,999.99")
        assert price == 1999.99


# ── Title Normalizer Tests ─────────────────────────────────────────────────────

class TestTitleNormalizer:
    def test_lowercase(self):
        assert normalize_title("HELLO WORLD") == "hello world"

    def test_trim(self):
        assert normalize_title("  hello  ") == "hello"

    def test_remove_punctuation(self):
        result = normalize_title("Black Hoodie - Men's (XL)")
        assert "-" not in result
        assert "'" not in result
        assert "(" not in result

    def test_collapse_spaces(self):
        result = normalize_title("black   hoodie")
        assert "  " not in result

    def test_unicode_normalization(self):
        result = normalize_title("café")
        assert isinstance(result, str)

    def test_empty(self):
        assert normalize_title("") == ""
        assert normalize_title(None) == ""

    def test_matching(self):
        a = normalize_title("Black Hoodie - Men's XL")
        b = normalize_title("black hoodie mens xl")
        # Both should be very similar
        assert "black" in a and "black" in b
        assert "hoodie" in a and "hoodie" in b


# ── URL Normalizer Tests ────────────────────────────────────────────────────────

class TestUrlNormalizer:
    def test_absolute_url_unchanged(self):
        url = "https://example.com/product/123"
        result = normalize_url(url, "https://example.com")
        assert result == url

    def test_relative_url(self):
        result = normalize_url("/product/123", "https://example.com")
        assert result == "https://example.com/product/123"

    def test_relative_no_slash(self):
        result = normalize_url("product/123", "https://example.com/shop/")
        assert "example.com" in result


# ── Change Detection Tests ─────────────────────────────────────────────────────

class TestPriceEvents:
    def test_price_decrease_event_type(self):
        from app.services.detection import _get_price_event_type
        from decimal import Decimal
        assert _get_price_event_type(Decimal("50.00"), 40.0) == "price_decrease"

    def test_price_increase_event_type(self):
        from app.services.detection import _get_price_event_type
        from decimal import Decimal
        assert _get_price_event_type(Decimal("40.00"), 50.0) == "price_increase"

    def test_prices_differ(self):
        from app.services.detection import _prices_differ
        from decimal import Decimal
        assert _prices_differ(Decimal("39.99"), 29.99) == True
        assert _prices_differ(Decimal("39.99"), 39.99) == False
        assert _prices_differ(None, 10.0) == True
        assert _prices_differ(None, None) == False

    def test_existing_product_matching_does_not_collapse_same_titles(self):
        from types import SimpleNamespace
        from app.services.detection import _index_existing_products, _match_existing_product

        existing = SimpleNamespace(
            url="https://store.test/products/chill-knife",
            external_id=None,
            normalized_title="chill",
        )
        by_url, by_external_id = _index_existing_products([existing])

        assert _match_existing_product(
            "https://store.test/products/chill-gun",
            None,
            by_url,
            by_external_id,
        ) is None


# ── Scraper HTML Parsing Tests ─────────────────────────────────────────────────

class TestScraperParsing:
    """Mock HTML parsing tests."""

    def test_detect_stock_in(self):
        from app.services.scraper import _detect_stock
        assert _detect_stock("In Stock") == "in_stock"
        assert _detect_stock("Add to Cart") == "in_stock"
        assert _detect_stock("Available") == "in_stock"

    def test_detect_stock_out(self):
        from app.services.scraper import _detect_stock
        assert _detect_stock("Out of Stock") == "out_of_stock"
        assert _detect_stock("Sold Out") == "out_of_stock"
        assert _detect_stock("Unavailable") == "out_of_stock"

    def test_detect_stock_unknown(self):
        from app.services.scraper import _detect_stock
        assert _detect_stock(None) == "unknown"
        assert _detect_stock("") == "unknown"
        assert _detect_stock("Some random text") == "unknown"


# ── Notification Routing Tests ───────────────────────────────────────────────

class TestNotificationRouting:
    def test_competitor_webhook_takes_priority(self):
        from types import SimpleNamespace
        from app.services.notification import get_notification_webhook_url

        competitor = SimpleNamespace(discord_webhook_url="https://example.com/competitor")
        result = get_notification_webhook_url(competitor, "https://example.com/default")
        assert result == "https://example.com/competitor"

    def test_default_webhook_used_when_competitor_has_none(self):
        from types import SimpleNamespace
        from app.services.notification import get_notification_webhook_url

        competitor = SimpleNamespace(discord_webhook_url=None)
        result = get_notification_webhook_url(competitor, "https://example.com/default")
        assert result == "https://example.com/default"


# ── Shopify / Search Tests ───────────────────────────────────────────────────

class TestShopifyScraper:
    def test_extract_shopify_product_category(self):
        from app.services.scraper import _extract_shopify_product

        raw = {
            "id": 1,
            "title": "Rainbow Shiny Pet",
            "handle": "rainbow-shiny-pet",
            "variants": [{"id": 2, "available": True, "price": "3.50"}],
            "images": [{"src": "https://cdn.example/pet.png"}],
        }
        product = _extract_shopify_product(raw, "https://example.com", "Adopt Me")
        assert product["category"] == "Adopt Me"
        assert product["price"] == 3.50
        assert product["url"] == "https://example.com/products/rainbow-shiny-pet"


class TestCompetitorDefaults:
    def test_base_url_only_competitor_uses_shopify_catalog(self):
        from app.api.competitors import _normalize_competitor_payload

        payload = _normalize_competitor_payload({
            "name": "MM2Cheap",
            "base_url": "https://mm2.cheap/",
            "scrape_type": "generic_selector",
            "listing_urls": [],
            "selector_config": {},
        })
        assert payload["scrape_type"] == "shopify_json"
        assert payload["selector_config"]["discover_collections"] is True
        assert payload["selector_config"]["include_all_products"] is True

    def test_shopify_catalog_defaults_include_all_products(self):
        from app.api.competitors import _normalize_competitor_payload

        payload = _normalize_competitor_payload({
            "scrape_type": "shopify_json",
            "selector_config": {"collection_handles": ["mm2"]},
        })
        assert payload["selector_config"]["collection_handles"] == ["mm2"]
        assert payload["selector_config"]["include_all_products"] is True


class TestScanSafety:
    def test_empty_scrape_is_rejected_by_default(self):
        from app.workers.tasks import _should_reject_empty_scrape

        assert _should_reject_empty_scrape({"selector_config": {}}, []) is True

    def test_empty_scrape_can_be_explicitly_allowed(self):
        from app.workers.tasks import _should_reject_empty_scrape

        competitor = {"selector_config": {"allow_empty_catalog": True}}
        assert _should_reject_empty_scrape(competitor, []) is False


class TestFuzzySearch:
    def test_match_score_handles_imperfect_spelling(self):
        from app.api.search_dashboard_settings import _match_score

        assert _match_score("rainbow pet", "rainbow shiny pet") > 0.5
        assert _match_score("rainbo shiny", "rainbow shiny pet") > 0.4

    def test_comparison_alias_removes_weapon_descriptor(self):
        from app.api.search_dashboard_settings import _comparison_aliases, _comparison_score

        aliases = _comparison_aliases("chill knife")
        assert "chill" in aliases
        assert _comparison_score("chill", "chill") > 0.9
        assert _comparison_score("chill", "chillin chili") < 0.86

    def test_comparison_alias_is_symmetric_for_descriptors(self):
        from app.api.search_dashboard_settings import _comparison_aliases, _comparison_score

        target_aliases = _comparison_aliases("chill")
        candidate_aliases = _comparison_aliases("chill knife")
        score = max(
            _comparison_score(target, candidate)
            for target in target_aliases
            for candidate in candidate_aliases
        )
        assert score > 0.9
