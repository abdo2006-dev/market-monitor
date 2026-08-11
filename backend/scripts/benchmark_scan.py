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

Why this exists: the deployment topology decision (docs/adr/0006) cannot be made
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
import statistics
import sys
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal, engine  # noqa: E402
from app.models import Competitor  # noqa: E402
from app.services.scraper import scrape_competitor  # noqa: E402

BROWSER_STRATEGIES = {"generic_selector", "custom"}


class NetworkMeter:
    """Counts HTTP requests and total time spent in them, across both clients."""

    def __init__(self) -> None:
        self.count = 0
        self.total_seconds = 0.0
        self.durations: list[float] = []
        self.errors = 0

    def record(self, seconds: float, failed: bool = False) -> None:
        self.count += 1
        self.total_seconds += seconds
        self.durations.append(seconds)
        if failed:
            self.errors += 1

    @property
    def slowest(self) -> float:
        return max(self.durations) if self.durations else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.durations) if self.durations else 0.0


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
        try:
            return await original_aiohttp(self, method, url, **kwargs)
        except Exception:
            failed = True
            raise
        finally:
            meter.record(time.perf_counter() - start, failed)

    async def timed_httpx(self, request, **kwargs):
        start = time.perf_counter()
        failed = False
        try:
            return await original_httpx(self, request, **kwargs)
        except Exception:
            failed = True
            raise
        finally:
            meter.record(time.perf_counter() - start, failed)

    aiohttp.ClientSession._request = timed_aiohttp
    httpx.AsyncClient.send = timed_httpx
    try:
        yield meter
    finally:
        aiohttp.ClientSession._request = original_aiohttp
        httpx.AsyncClient.send = original_httpx


async def benchmark_one(competitor: Competitor, max_pages: int) -> dict:
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
        error = f"{type(exc).__name__}: {exc}"

    duration = time.perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    priced = [p for p in products if p.get("price") is not None]
    categories = {p.get("category") for p in products if p.get("category")}

    return {
        "competitor_id": competitor.id,
        "competitor_name": competitor.name,
        "base_url": competitor.base_url,
        "scrape_type": competitor.scrape_type,
        "uses_browser": competitor.scrape_type in BROWSER_STRATEGIES,
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
        "peak_memory_mb": round(peak / 1_048_576, 1),
        "error": error,
        "bucket": _bucket(duration, error, len(products)),
    }


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


async def load_competitors(competitor_id: int | None, include_inactive: bool):
    async with AsyncSessionLocal() as session:
        stmt = select(Competitor).order_by(Competitor.name)
        if competitor_id is not None:
            stmt = stmt.where(Competitor.id == competitor_id)
        elif not include_inactive:
            stmt = stmt.where(Competitor.active == True)  # noqa: E712
        return (await session.execute(stmt)).scalars().all()


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
            f"{name:<22} {r['scrape_type']:<17} {marker:<8} "
            f"{r['duration_seconds']:>7.1f}s {r['products_found']:>9} "
            f"{r['http_requests']:>6} {r['http_seconds_total']:>6.1f}s "
            f"{r['peak_memory_mb']:>6.1f}M  {r['bucket']}"
        )
        if r["error"]:
            print(f"{'':<22} ERROR: {r['error'][:90]}")
    print("=" * len(header))

    ok = [r for r in results if not r["error"] and r["products_found"] > 0]
    failed = [r for r in results if r["error"] or r["products_found"] == 0]
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
                        help="run N competitors at once (default 1 = serial)")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--json", metavar="PATH", help="also write raw results as JSON")
    args = parser.parse_args()

    competitors = await load_competitors(args.competitor, args.include_inactive)
    if not competitors:
        print("No competitors matched. Is DATABASE_URL pointing at the right database?")
        await engine.dispose()
        return 1

    print(f"Benchmarking {len(competitors)} competitor(s), max_pages={args.max_pages}, "
          f"concurrency={args.concurrency}")
    print("This makes real requests to real sites.\n")

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
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"\nWrote {args.json}")
        print("Review before sharing: it contains competitor names and base URLs.")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
