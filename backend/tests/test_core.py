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
