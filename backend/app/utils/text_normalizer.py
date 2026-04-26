import re
import unicodedata
from typing import Optional


def normalize_title(title: Optional[str]) -> str:
    """
    Normalize product title for matching:
    - lowercase
    - trim spaces
    - remove punctuation
    - normalize unicode
    - collapse whitespace
    """
    if not title:
        return ""
    # Unicode normalization
    text = unicodedata.normalize("NFKD", title)
    # Lowercase
    text = text.lower()
    # Remove punctuation except alphanumerics and spaces
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_url(url: str, base_url: str) -> str:
    """Make relative URLs absolute using base_url."""
    from urllib.parse import urljoin, urlparse
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    return urljoin(base_url, url)


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = normalize_title(text)
    return re.sub(r"\s+", "-", text)
