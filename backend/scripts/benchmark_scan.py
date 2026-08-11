#!/usr/bin/env python
"""
Measure how the real scrapers actually behave, without touching stored data.

This calls `services.scraper.scrape_competitor` directly — the same acquisition
code a sync uses — but NEVER runs detection, never opens a write transaction,
never creates a ScrapeRun, and never sends a notification. The database is read
once, to load competitor configuration.

    python scripts/benchmark_scan.py                    # every active competitor
    python scripts/benchmark_scan.py --competitor 3     # one competitor
    python scripts/benchmark_scan.py --max-pages 2      # cheaper probe
    python scripts/benchmark_scan.py --json out.json    # machine-readable

Why this exists: the deployment topology proposal (docs/adr/0008) cannot be made
without knowing which competitors finish in seconds and which need a browser.
See docs/DAILY_CRITICAL_WORKFLOWS.md "Measuring the real workload".

This makes real requests to real competitor sites. Do not run it in CI, and do
not run it in a tight loop.

Output NEVER includes selector_config, which can contain third-party Storefront
access tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
import tracemalloc
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.models import Competitor  # noqa: E402
from app.services.scraper import scrape_competitor  # noqa: E402


@dataclass(frozen=True)
class BenchmarkCompetitor:
    id: int
    name: str
    base_url: str
    listing_urls: list[str]
    selector_config: dict
    scrape_type: str

    @property
    def uses_browser(self) -> bool:
        # generic_selector with no listing URLs follows the Shopify HTTP path.
        return self.scrape_type not in {"shopify_json", "salla_json"} and bool(
            self.listing_urls
        )


class NetworkMeter:
    """Counts HTTP requests and total time spent in them, across both clients."""

    def __init__(self) -> None:
        self.count = 0
        self.total_seconds = 0.0
        self.durations: list[float] = []
        self.errors = 0
        self.catalog_pages = 0
        self.throttled_responses = 0
        self.server_error_responses = 0
        self.status_codes: Counter[int] = Counter()
        self._get_request_keys: Counter[str] = Counter()

    def record(
        self,
        method: str,
        url,
        seconds: float,
        status: int | None = None,
        failed: bool = False,
    ) -> None:
        self.count += 1
        self.total_seconds += seconds
        self.durations.append(seconds)
        if failed:
            self.errors += 1
        if status is not None:
            self.status_codes[status] += 1
            if status == 429:
                self.throttled_responses += 1
            if status >= 500:
                self.server_error_responses += 1
            if status < 400 and _is_catalog_page_request(method, url):
                self.catalog_pages += 1
        if method.upper() == "GET":
            self._get_request_keys[str(url)] += 1

    @property
    def slowest(self) -> float:
        return max(self.durations) if self.durations else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.durations) if self.durations else 0.0

    @property
    def repeated_get_requests(self) -> int:
        """Best-effort count of fallback/repeated GETs, without exposing URLs."""
        return sum(max(0, count - 1) for count in self._get_request_keys.values())


def _is_catalog_page_request(method: str, url) -> bool:
    parsed = urlsplit(str(url))
    path = parsed.path.lower()
    return (
        path.endswith("/products.json")
        or "/api/v1/products" in path
        or (method.upper() == "POST" and path.endswith("/graphql.json"))
    )


@contextmanager
def measure_network(meter: NetworkMeter):
    """
    Instrument aiohttp and httpx for the duration of one scrape.

    Patches are reverted on exit, so this cannot leak into anything else.
    """
    import aiohttp
    import httpx

    original_aiohttp = aiohttp.ClientSession._request
    original_httpx = httpx.AsyncClient.send

    async def timed_aiohttp(self, method, url, **kwargs):
        start = time.perf_counter()
        failed = False
        status = None
        try:
            response = await original_aiohttp(self, method, url, **kwargs)
            status = response.status
            return response
        except Exception:
            failed = True
            raise
        finally:
            meter.record(method, url, time.perf_counter() - start, status, failed)

    async def timed_httpx(self, request, **kwargs):
        start = time.perf_counter()
        failed = False
        status = None
        try:
            response = await original_httpx(self, request, **kwargs)
            status = response.status_code
            return response
        except Exception:
            failed = True
            raise
        finally:
            meter.record(
                request.method,
                request.url,
                time.perf_counter() - start,
                status,
                failed,
            )

    aiohttp.ClientSession._request = timed_aiohttp
    httpx.AsyncClient.send = timed_httpx
    try:
        yield meter
    finally:
        aiohttp.ClientSession._request = original_aiohttp
        httpx.AsyncClient.send = original_httpx


async def benchmark_one(competitor: BenchmarkCompetitor, max_pages: int) -> dict:
    payload = {
        "id": competitor.id,
        "base_url": competitor.base_url,
        "listing_urls": competitor.listing_urls or [],
        "selector_config": competitor.selector_config or {},
        "scrape_type": competitor.scrape_type,
    }

    meter = NetworkMeter()
    tracemalloc.start()
    start = time.perf_counter()
    products: list[dict] = []
    error = None

    try:
        with measure_network(meter):
            products = await scrape_competitor(
                payload,
                max_pages=max_pages,
                page_delay=settings.DEFAULT_PAGE_DELAY_SECONDS,
                headless=settings.PLAYWRIGHT_HEADLESS,
                user_agent=settings.USER_AGENT,
            )
    except Exception as exc:
        error = _failure_category(exc)

    duration = time.perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    priced = [p for p in products if p.get("price") is not None]
    categories = {p.get("category") for p in products if p.get("category")}

    return {
        "competitor_id": competitor.id,
        "competitor_name": competitor.name,
        "acquisition_strategy": competitor.scrape_type,
        "uses_browser": competitor.uses_browser,
        "listing_url_count": len(competitor.listing_urls or []),
        "max_pages": max_pages,
        "duration_seconds": round(duration, 2),
        "products_found": len(products),
        "products_with_price": len(priced),
        "distinct_categories": len(categories),
        "http_requests": meter.count,
        "http_seconds_total": round(meter.total_seconds, 2),
        "http_seconds_median": round(meter.median, 3),
        "http_seconds_slowest": round(meter.slowest, 2),
        "http_errors": meter.errors,
        "catalog_pages_fetched": meter.catalog_pages,
        "request_measurement_scope": "aiohttp_and_httpx_only",
        "explicit_retries": 0,
        "repeated_get_requests": meter.repeated_get_requests,
        "throttled_responses": meter.throttled_responses,
        "server_error_responses": meter.server_error_responses,
        "http_status_counts": dict(sorted(meter.status_codes.items())),
        "peak_memory_mb": round(peak / 1_048_576, 1),
        "failure_category": error,
        "completeness": "not_observable" if products else "no_products",
        "bucket": _bucket(duration, error, len(products)),
    }


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    name = type(exc).__name__.lower()
    if "connect" in name or "network" in name:
        return "network_error"
    if "playwright" in name or "browser" in name:
        return "browser_error"
    return type(exc).__name__


def _bucket(duration: float, error: str | None, product_count: int) -> str:
    if error:
        return "failed"
    if product_count == 0:
        # The scraper swallows connection errors and returns [] rather than
        # raising (docs/SCRAPING_ARCHITECTURE.md §1.7), so an empty result is
        # NOT a success. A real sync would treat this as a failed scan via
        # _should_reject_empty_scrape.
        return "EMPTY (would fail a real sync)"
    if duration < 10:
        return "<10s"
    if duration < 30:
        return "10-30s"
    if duration < 60:
        return "30-60s"
    return ">60s"


async def load_competitors(
    competitor_id: int | None, include_inactive: bool
) -> list[BenchmarkCompetitor]:
    """Load only acquisition configuration in an enforced read-only transaction."""
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        stmt = select(
            Competitor.id,
            Competitor.name,
            Competitor.base_url,
            Competitor.listing_urls,
            Competitor.selector_config,
            Competitor.scrape_type,
        ).order_by(Competitor.name)
        if competitor_id is not None:
            stmt = stmt.where(Competitor.id == competitor_id)
        elif not include_inactive:
            stmt = stmt.where(Competitor.active == True)  # noqa: E712
        rows = (await connection.execute(stmt)).mappings().all()
        await connection.rollback()
    return [
        BenchmarkCompetitor(
            id=row["id"],
            name=row["name"],
            base_url=row["base_url"],
            listing_urls=list(row["listing_urls"] or []),
            selector_config=dict(row["selector_config"] or {}),
            scrape_type=row["scrape_type"],
        )
        for row in rows
    ]


def print_table(results: list[dict], total_wall: float, concurrency: int) -> None:
    header = (
        f"{'Competitor':<22} {'Strategy':<17} {'Browser':<8} "
        f"{'Time':>8} {'Products':>9} {'Reqs':>6} {'Net':>7} {'Mem':>7}  Bucket"
    )
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        name = r["competitor_name"][:21]
        marker = "yes" if r["uses_browser"] else "no"
        print(
            f"{name:<22} {r['acquisition_strategy']:<17} {marker:<8} "
            f"{r['duration_seconds']:>7.1f}s {r['products_found']:>9} "
            f"{r['http_requests']:>6} {r['http_seconds_total']:>6.1f}s "
            f"{r['peak_memory_mb']:>6.1f}M  {r['bucket']}"
        )
        if r["failure_category"]:
            print(f"{'':<22} ERROR: {r['failure_category']}")
    print("=" * len(header))

    ok = [
        r for r in results
        if not r["failure_category"] and r["products_found"] > 0
    ]
    failed = [
        r for r in results
        if r["failure_category"] or r["products_found"] == 0
    ]
    buckets: dict[str, int] = {}
    for r in results:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1

    print(f"  competitors           : {len(results)} ({len(failed)} failed)")
    print(f"  products found        : {sum(r['products_found'] for r in ok)}")
    if ok:
        print(f"  slowest competitor    : {max(r['duration_seconds'] for r in ok):.1f}s")
        print(f"  sum of durations      : {sum(r['duration_seconds'] for r in ok):.1f}s"
              "   <- serial 'scan all' cost")
    print(f"  measured wall clock   : {total_wall:.1f}s   (concurrency={concurrency})")
    print(f"  needs a browser       : {sum(1 for r in results if r['uses_browser'])}")
    print(f"  throttled responses   : {sum(r['throttled_responses'] for r in results)}")
    print("  buckets               : "
          + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    print("=" * len(header))
    print("  Topology reference points (docs/adr/0006):")
    print("    Vercel serverless request limit ....... 300s")
    print("    Celery task_soft_time_limit ........... 600s")
    print("  If 'sum of durations' approaches 300s, a serial cron-driven scan-all")
    print("  cannot complete inside one serverless invocation.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competitor", type=int, help="benchmark a single competitor id")
    parser.add_argument("--max-pages", type=int, default=settings.DEFAULT_MAX_PAGES)
    parser.add_argument("--concurrency", type=int, default=1,
                        help="reserved; safety benchmark currently requires 1 (serial)")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--json", metavar="PATH", help="also write raw results as JSON")
    args = parser.parse_args()
    if args.concurrency != 1:
        parser.error(
            "this safety benchmark is intentionally sequential; estimate bounded "
            "concurrency from per-competitor durations instead of load-testing sites"
        )

    try:
        competitors = await load_competitors(args.competitor, args.include_inactive)
    except Exception as exc:
        print(
            f"Could not load competitor configuration ({_failure_category(exc)}).",
            file=sys.stderr,
        )
        await engine.dispose()
        return 2
    if not competitors:
        print("No competitors matched. Is DATABASE_URL pointing at the right database?")
        await engine.dispose()
        return 1

    print(f"Benchmarking {len(competitors)} competitor(s), max_pages={args.max_pages}, "
          f"concurrency={args.concurrency}")
    print("This makes real requests to real sites.\n")

    # Scraper warnings include request URLs. The benchmark emits sanitized
    # status/request counters instead, so captured output is safe to retain.
    logging.getLogger("app.services.scraper").setLevel(logging.CRITICAL)

    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def run(competitor):
        async with semaphore:
            return await benchmark_one(competitor, args.max_pages)

    started = time.perf_counter()
    results = await asyncio.gather(*(run(c) for c in competitors))
    total_wall = time.perf_counter() - started

    results = sorted(results, key=lambda r: -r["duration_seconds"])
    print_table(results, total_wall, args.concurrency)

    if args.json:
        payload = {
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "max_pages": args.max_pages,
            "concurrency": args.concurrency,
            "total_wall_seconds": round(total_wall, 2),
            "results": results,
        }
        output_path = Path(args.json)
        output_path.write_text(json.dumps(payload, indent=2))
        output_path.chmod(0o600)
        print(f"\nWrote {args.json}")
        print("Output is sanitized: no database URL, storefront URL, selector config, or token.")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
