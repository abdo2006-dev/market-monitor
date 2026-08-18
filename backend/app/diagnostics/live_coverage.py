"""Gentle read-only live smoke for the canonical competitor set.

Run manually, never as required deterministic CI:

    python -m app.diagnostics.live_coverage
    python -m app.diagnostics.live_coverage --json /tmp/coverage.json

The runner invokes acquisition directly. It imports no model and opens no
database session, so it cannot write Product, Event, Snapshot, ScrapeRun, or
SyncRequest state. Output is deliberately sanitized.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.config import settings
from app.diagnostics.coverage_registry import CoverageCompetitor, get_live_competitors
from app.domain.acquisition import AcquisitionFailure, acquire_catalog
from app.domain.product_identity import canonicalize_product_url, product_identity_key


STATUSES = {"HEALTHY", "DEGRADED", "PARTIAL", "SUSPICIOUS_EMPTY", "FAILED"}


@dataclass
class NetworkEvidence:
    request_count: int = 0
    retries: int = 0
    throttled_429: int = 0
    server_errors_5xx: int = 0
    status_counts: Counter = field(default_factory=Counter)

    def record(self, status: int | None) -> None:
        self.request_count += 1
        if status is not None:
            self.status_counts[status] += 1
            self.throttled_429 += int(status == 429)
            self.server_errors_5xx += int(status >= 500)


@contextmanager
def measure_network(evidence: NetworkEvidence):
    """Count aiohttp/httpx requests without retaining URLs, bodies, or headers."""
    import aiohttp
    import httpx

    original_aiohttp = aiohttp.ClientSession._request
    original_httpx = httpx.AsyncClient.send

    async def measured_aiohttp(self, method, url, **kwargs):
        status = None
        try:
            response = await original_aiohttp(self, method, url, **kwargs)
            status = response.status
            return response
        finally:
            evidence.record(status)

    async def measured_httpx(self, request, **kwargs):
        status = None
        try:
            response = await original_httpx(self, request, **kwargs)
            status = response.status_code
            return response
        finally:
            evidence.record(status)

    aiohttp.ClientSession._request = measured_aiohttp
    httpx.AsyncClient.send = measured_httpx
    try:
        yield
    finally:
        aiohttp.ClientSession._request = original_aiohttp
        httpx.AsyncClient.send = original_httpx


@dataclass(frozen=True)
class CoverageResult:
    competitor: str
    configured_strategy: str
    detected_strategy: str
    reachable: bool
    products_observed: int
    valid_price_count: int
    price_parse_coverage: float
    currencies: dict[str, int]
    duplicate_identity_count: int
    duplicate_canonical_url_count: int
    invalid_identity_count: int
    completeness: str
    page_cap_reached: bool
    suspicious_empty: bool
    pages_fetched: int
    request_count: int
    retries: int
    throttled_429: int
    server_errors_5xx: int
    browser_automation_required: bool
    duration_seconds: float
    result: str
    warning: str | None
    failure_category: str | None


def _identity_evidence(observations: list[dict], scrape_type: str) -> tuple[int, int, int]:
    canonical_counts: Counter[str] = Counter()
    identity_counts: Counter[str] = Counter()
    invalid = 0
    for item in observations:
        try:
            canonical = canonicalize_product_url(item.get("url", ""), scrape_type)
        except Exception:
            invalid += 1
            continue
        canonical_counts[canonical] += 1
        identity = product_identity_key(item.get("external_id"))
        if identity:
            identity_counts[identity] += 1
    duplicate_urls = sum(max(0, count - 1) for count in canonical_counts.values())
    duplicate_ids = sum(max(0, count - 1) for count in identity_counts.values())
    return duplicate_ids, duplicate_urls, invalid


def _price_evidence(observations: list[dict]) -> tuple[int, float, dict[str, int], list[Decimal]]:
    valid = 0
    prices: list[Decimal] = []
    currencies: Counter[str] = Counter()
    for item in observations:
        currency = str(item.get("currency") or "").upper()
        if len(currency) == 3:
            currencies[currency] += 1
        raw_price = item.get("price")
        if raw_price is None:
            continue
        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, ValueError):
            continue
        if price.is_finite() and price >= 0:
            valid += 1
            prices.append(price)
    coverage = valid / len(observations) if observations else 0.0
    return valid, coverage, dict(sorted(currencies.items())), prices


def _previous_counts(path: str | None) -> dict[str, int]:
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(item.get("competitor")): int(item.get("products_observed") or 0)
        for item in payload.get("results", [])
        if isinstance(item, dict) and item.get("competitor")
    }


async def run_one(
    competitor: CoverageCompetitor,
    *,
    max_pages: int,
    timeout_seconds: float,
    previous_count: int | None,
) -> CoverageResult:
    network = NetworkEvidence()
    started = time.perf_counter()
    acquisition = None
    failure_category = None
    try:
        with measure_network(network):
            acquisition = await asyncio.wait_for(
                acquire_catalog(
                    competitor.acquisition_payload(),
                    max_pages=max_pages,
                    page_delay=settings.DEFAULT_PAGE_DELAY_SECONDS,
                    headless=settings.PLAYWRIGHT_HEADLESS,
                    user_agent=settings.USER_AGENT,
                ),
                timeout=timeout_seconds,
            )
    except AcquisitionFailure as exc:
        failure_category = exc.category
    except asyncio.TimeoutError:
        failure_category = "coverage_timeout"
    except Exception as exc:
        # Exception class is safe diagnostic taxonomy; raw text may contain a
        # URL, token, cookie, or environment value and is never emitted.
        failure_category = type(exc).__name__.lower()[:80]
    duration = time.perf_counter() - started

    observations = acquisition.observations if acquisition else []
    valid_prices, price_coverage, currencies, prices = _price_evidence(observations)
    duplicate_ids, duplicate_urls, invalid_ids = _identity_evidence(
        observations, competitor.scrape_type
    )
    completeness = acquisition.completeness if acquisition else "failed"
    detected_strategy = acquisition.strategy if acquisition else competitor.scrape_type
    page_cap = bool(acquisition and acquisition.page_cap_reached)
    suspicious_empty = competitor.expected_non_empty and not observations
    warnings: list[str] = []

    if failure_category:
        status = "FAILED"
        warnings.append(f"safe failure category: {failure_category}")
    elif suspicious_empty or completeness == "suspicious_empty":
        status = "SUSPICIOUS_EMPTY"
        warnings.append("normally non-empty storefront returned zero products")
    elif completeness == "partial" or page_cap:
        status = "PARTIAL"
        warnings.append(acquisition.completeness_reason or "catalog coverage is partial")
    else:
        status = "HEALTHY"

    if observations and detected_strategy not in competitor.expected_strategies:
        warnings.append(f"strategy changed to {detected_strategy}")
        status = "DEGRADED" if status == "HEALTHY" else status
    if observations and price_coverage < 0.75:
        warnings.append(f"price parse coverage is {price_coverage:.1%}")
        status = "DEGRADED" if status == "HEALTHY" else status
    if duplicate_ids or duplicate_urls or invalid_ids:
        warnings.append(
            f"identity anomalies: ids={duplicate_ids}, urls={duplicate_urls}, invalid={invalid_ids}"
        )
        status = "DEGRADED" if status == "HEALTHY" else status
    if len(prices) >= 10 and len(set(prices)) == 1:
        warnings.append("all parsed prices are identical")
        status = "DEGRADED" if status == "HEALTHY" else status
    if len(observations) >= 10 and all(
        item.get("stock_status") == "out_of_stock" for item in observations
    ):
        warnings.append("every observed product is out of stock")
        status = "DEGRADED" if status == "HEALTHY" else status
    baseline = previous_count or competitor.previous_product_count
    if baseline and baseline >= 20 and observations and len(observations) < baseline * 0.5:
        warnings.append(f"product count fell materially from prior smoke ({baseline})")
        status = "DEGRADED" if status == "HEALTHY" else status
    if competitor.caveat and status != "HEALTHY":
        warnings.append(competitor.caveat)

    assert status in STATUSES
    return CoverageResult(
        competitor=competitor.name,
        configured_strategy=competitor.scrape_type,
        detected_strategy=detected_strategy,
        reachable=network.request_count > 0 and failure_category != "coverage_timeout",
        products_observed=len(observations),
        valid_price_count=valid_prices,
        price_parse_coverage=round(price_coverage, 4),
        currencies=currencies,
        duplicate_identity_count=duplicate_ids,
        duplicate_canonical_url_count=duplicate_urls,
        invalid_identity_count=invalid_ids,
        completeness=completeness,
        page_cap_reached=page_cap,
        suspicious_empty=suspicious_empty,
        pages_fetched=acquisition.pages_fetched if acquisition else 0,
        request_count=network.request_count,
        retries=network.retries,
        throttled_429=network.throttled_429,
        server_errors_5xx=network.server_errors_5xx,
        browser_automation_required=competitor.scrape_type == "generic_selector",
        duration_seconds=round(duration, 2),
        result=status,
        warning="; ".join(warnings)[:500] or None,
        failure_category=failure_category,
    )


def print_matrix(results: list[CoverageResult]) -> None:
    print(
        f"{'Competitor':<16} {'Strategy':<29} {'Products':>8} {'Price':>7} "
        f"{'Complete':<18} {'Dup':>4} {'Time':>7}  Result"
    )
    print("-" * 112)
    for item in results:
        print(
            f"{item.competitor[:15]:<16} {item.detected_strategy[:28]:<29} "
            f"{item.products_observed:>8} {item.price_parse_coverage:>6.1%} "
            f"{item.completeness[:17]:<18} "
            f"{item.duplicate_identity_count + item.duplicate_canonical_url_count:>4} "
            f"{item.duration_seconds:>6.1f}s  {item.result}"
        )
        if item.warning:
            print(f"  warning: {item.warning}")


async def async_main(args: argparse.Namespace) -> int:
    competitors = get_live_competitors(set(args.only or []))
    if args.only and len(competitors) != len(set(args.only)):
        print("One or more --only names are not in the canonical registry.")
        return 2
    prior = _previous_counts(args.previous)
    results = []
    for competitor in competitors:
        results.append(
            await run_one(
                competitor,
                max_pages=args.max_pages,
                timeout_seconds=args.timeout_seconds,
                previous_count=prior.get(competitor.name),
            )
        )
    print_matrix(results)
    print("-" * 112)
    print(
        f"{len(results)} competitors; "
        f"{sum(item.products_observed for item in results)} products; "
        f"{sum(item.request_count for item in results)} requests; "
        f"429={sum(item.throttled_429 for item in results)}; "
        f"5xx={sum(item.server_errors_5xx for item in results)}; "
        f"median={statistics.median([item.duration_seconds for item in results]) if results else 0:.2f}s"
    )

    if args.json:
        payload = {
            "schema_version": 1,
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only_live_acquisition",
            "max_pages": args.max_pages,
            "results": [asdict(item) for item in results],
        }
        path = Path(args.json)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        path.chmod(0o600)
        print(f"Sanitized JSON written to {path}")

    unhealthy = any(item.result != "HEALTHY" for item in results)
    return 1 if args.fail_on_unhealthy and unhealthy else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", help="run one canonical competitor by name")
    parser.add_argument("--max-pages", type=int, default=settings.DEFAULT_MAX_PAGES)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--previous", help="prior sanitized JSON artifact for drop checks")
    parser.add_argument("--json", metavar="PATH", help="write a sanitized JSON artifact")
    parser.add_argument(
        "--fail-on-unhealthy",
        action="store_true",
        help="non-zero exit for release gates; omitted for observational smoke",
    )
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 100:
        parser.error("--max-pages must be between 1 and 100")
    if not 1 <= args.timeout_seconds <= 900:
        parser.error("--timeout-seconds must be between 1 and 900")

    logging.getLogger("app.services.scraper").setLevel(logging.CRITICAL)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
