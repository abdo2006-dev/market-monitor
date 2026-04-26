import re
from typing import Optional, Tuple


CURRENCY_SYMBOLS = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY",
    "₹": "INR", "₩": "KRW", "₪": "ILS", "₫": "VND",
    "฿": "THB", "zł": "PLN", "kr": "SEK", "CHF": "CHF",
    "CAD": "CAD", "AUD": "AUD", "NZD": "NZD",
}

CURRENCY_CODES = {"USD", "EUR", "GBP", "JPY", "INR", "KRW", "CAD", "AUD", "CHF", "NZD", "SEK", "PLN"}


def parse_price(raw: Optional[str], default_currency: str = "USD") -> Tuple[Optional[float], str]:
    """
    Robustly parse price strings like:
    "$39.99", "USD 39.99", "39,99", "€39.99", "1.299,00", "1,299.00"
    Returns (price_float, currency_code)
    """
    if not raw:
        return None, default_currency

    text = raw.strip()

    # Detect currency symbol
    currency = default_currency
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            text = text.replace(symbol, "")
            break

    # Check for currency codes
    for code in CURRENCY_CODES:
        if code in text.upper():
            currency = code
            text = re.sub(re.escape(code), "", text, flags=re.IGNORECASE)
            break

    # Remove non-numeric except digits, commas, dots, minus
    text = re.sub(r"[^\d.,-]", "", text).strip()

    if not text:
        return None, currency

    # Handle different decimal formats
    # European: 1.299,00 -> 1299.00
    # US: 1,299.00 -> 1299.00
    # Simple comma decimal: 39,99 -> 39.99
    if "," in text and "." in text:
        # Both present - determine which is decimal separator
        last_comma = text.rfind(",")
        last_dot = text.rfind(".")
        if last_comma > last_dot:
            # European format: 1.299,00
            text = text.replace(".", "").replace(",", ".")
        else:
            # US format: 1,299.00
            text = text.replace(",", "")
    elif "," in text:
        # Could be 39,99 (European decimal) or 1,000 (thousands separator)
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # Decimal separator: 39,99
            text = text.replace(",", ".")
        else:
            # Thousands separator
            text = text.replace(",", "")

    try:
        price = float(text)
        if price < 0:
            return None, currency
        return round(price, 2), currency
    except ValueError:
        return None, currency
