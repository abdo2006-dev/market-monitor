"""Truthful, synchronous collection exports.

The endpoint deliberately remains a direct download for the daily workflow.
Its file bodies preserve the established CSV/JSONL product schemas; file-level
truth travels in typed ``X-Market-Monitor-Export-*`` response headers instead
of silently substituting stored rows for a failed live acquisition.
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import re
from datetime import datetime, timezone
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.domain.acquisition import AcquisitionFailure, AcquisitionResult, acquire_catalog
from app.domain.search_trust import (
    CompetitorEvidence,
    RunEvidence,
    assess_catalog_coverage,
)
from app.models import Competitor, Product, ScrapeRun
from app.schemas import CollectionExportFailure, CollectionExportProvenance
from app.utils.text_normalizer import normalize_title


router = APIRouter(prefix="/api/exports", tags=["exports"])

EXPORT_FIELDS = [
    "competitor_name",
    "competitor_base_url",
    "collection_url",
    "category",
    "title",
    "price",
    "currency",
    "stock_status",
    "sku",
    "external_id",
    "product_url",
    "image_url",
    "scraped_at",
]
EXPORT_PROVENANCE_FIELDS = ["observed_at", "observed_run_id", "coverage_state"]
EXPORT_HEADER_PREFIX = "X-Market-Monitor-Export-"


@router.get("/collection-prices")
async def export_collection_prices(
    competitor_id: int,
    collection_url: str = Query(..., min_length=1),
    format: Literal["csv", "jsonl", "json"] = Query("csv"),
    max_pages: int = Query(5, ge=1, le=20),
    mode: Literal["live", "cached"] = Query("live"),
    include_provenance: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Prepare one collection file without mutating durable Sync state.

    ``mode=live`` always reflects this acquisition only.  ``mode=cached`` is
    an explicit request for matching active stored products; it is never a
    fallback from the live path.
    """
    competitor = (
        await db.execute(select(Competitor).where(Competitor.id == competitor_id))
    ).scalar_one_or_none()
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    clean_collection_url = collection_url.strip()
    _validate_collection_url(competitor, clean_collection_url)

    if mode == "live":
        try:
            result = await acquire_collection(
                _collection_scrape_payload(competitor, clean_collection_url, max_pages),
                max_pages=max_pages,
                page_delay=settings.DEFAULT_PAGE_DELAY_SECONDS,
                headless=settings.PLAYWRIGHT_HEADLESS,
                user_agent=settings.USER_AGENT,
            )
        except AcquisitionFailure as exc:
            _raise_live_failure(exc.safe_message, category=exc.category)
        except Exception:
            # Do not expose adapter exceptions, request URLs, or provider detail.
            _raise_live_failure("The live collection could not be acquired. Retry it or choose stored data explicitly.")

        rows = _export_rows(
            competitor,
            clean_collection_url,
            result.observations,
            include_provenance=include_provenance,
            coverage_state=_live_coverage_state(result),
        )
        provenance = _live_provenance(result, products_count=len(rows))
    else:
        products = await _saved_collection_products(db, competitor, clean_collection_url)
        provenance = await _cached_provenance(db, competitor, products)
        if not products:
            failure = CollectionExportFailure(
                code="cached_export_unavailable",
                message="No active stored products match this collection. Run a live export first.",
                provenance=provenance,
            )
            raise HTTPException(status_code=404, detail=failure.model_dump(mode="json"))
        rows = _export_rows(
            competitor,
            clean_collection_url,
            products,
            include_provenance=include_provenance,
            coverage_state=provenance.coverage_state,
        )
        provenance = provenance.model_copy(update={"products_count": len(rows)})

    filename = _export_filename(competitor.name, clean_collection_url, format)
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        **_provenance_headers(provenance),
    }
    if format == "jsonl":
        body = "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in rows)
        if body:
            body += "\n"
        return Response(body, media_type="application/x-ndjson; charset=utf-8", headers=headers)
    if format == "json":
        body: dict = {
            "competitor": competitor.name,
            "collection_url": clean_collection_url,
            "products_count": len(rows),
            "items": rows,
        }
        if include_provenance:
            body["provenance"] = provenance.model_dump(mode="json")
        return Response(
            json.dumps(body, ensure_ascii=False, default=str, indent=2),
            media_type="application/json; charset=utf-8",
            headers=headers,
        )
    return Response(
        _csv_body(rows, include_provenance=include_provenance),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


async def acquire_collection(competitor: dict, **options) -> AcquisitionResult:
    """Patchable export boundary around the shared completeness-aware adapter."""
    return await acquire_catalog(competitor, **options)


def _raise_live_failure(_message: str, *, category: str | None = None) -> None:
    safe_reason = "Live acquisition failed"
    if category in {"invalid_configuration", "rate_limited", "temporary_network", "acquisition_error"}:
        safe_reason = category.replace("_", " ")
    provenance = CollectionExportProvenance(
        requested_mode="live",
        source="live",
        completeness="failed",
        products_count=0,
        safe_reason=safe_reason,
    )
    failure = CollectionExportFailure(
        # Adapter wording can contain store/provider details even when the
        # failure category is safe. Keep the browser contract deliberately
        # generic and let the durable Sync surface retain its own diagnostics.
        code="live_acquisition_failed",
        message="The live collection could not be acquired. Retry it or choose stored data explicitly.",
        provenance=provenance,
    )
    raise HTTPException(status_code=502, detail=failure.model_dump(mode="json"))


def _collection_scrape_payload(competitor: Competitor, collection_url: str, max_pages: int) -> dict:
    selector_config = dict(competitor.selector_config or {})
    selector_config["discover_collections"] = False
    selector_config["include_all_products"] = False
    selector_config["request_timeout_seconds"] = 8
    selector_config["max_sitemap_products"] = max(max_pages * 250, 250)

    handle = _collection_handle(collection_url)
    if handle:
        selector_config["collection_handles"] = [handle]
        selector_config["prefer_storefront_graphql"] = False

    return {
        "id": competitor.id,
        "base_url": competitor.base_url,
        "listing_urls": [collection_url],
        "selector_config": selector_config,
        "scrape_type": competitor.scrape_type or "shopify_json",
    }


def _export_rows(
    competitor: Competitor,
    collection_url: str,
    products: list[dict],
    *,
    include_provenance: bool = False,
    coverage_state: str | None = None,
) -> list[dict]:
    exported_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for product in products:
        row = {
            "competitor_name": competitor.name,
            "competitor_base_url": competitor.base_url,
            "collection_url": collection_url,
            "category": product.get("category"),
            "title": product.get("title"),
            "price": product.get("price"),
            "currency": product.get("currency") or "USD",
            "stock_status": product.get("stock_status") or "unknown",
            "sku": product.get("sku"),
            "external_id": product.get("external_id"),
            "product_url": product.get("url"),
            "image_url": product.get("image_url"),
            # Historical name preserved: this is file generation time, not an
            # observation claim. The provenance contract supplies actual evidence.
            "scraped_at": exported_at,
        }
        if include_provenance:
            observed_at = product.get("observed_at")
            row.update({
                "observed_at": observed_at.isoformat() if isinstance(observed_at, datetime) else None,
                "observed_run_id": product.get("observed_run_id"),
                "coverage_state": coverage_state,
            })
        rows.append(row)
    rows.sort(key=lambda row: ((row["title"] or "").lower(), row["price"] is None, row["price"] or 0))
    return rows


async def _saved_collection_products(
    db: AsyncSession, competitor: Competitor, collection_url: str
) -> list[dict]:
    result = await db.execute(
        select(Product)
        .where(Product.competitor_id == competitor.id, Product.active.is_(True))
        .order_by(Product.title.asc())
    )
    products = []
    for product in result.scalars().all():
        if not _product_matches_collection(product, collection_url):
            continue
        products.append({
            "title": product.title,
            "price": float(product.current_price) if product.current_price is not None else None,
            "currency": product.currency,
            "url": product.url,
            "image_url": product.image_url,
            "stock_status": product.stock_status,
            "sku": product.sku,
            "external_id": product.external_id,
            "category": product.category,
            "observed_at": product.last_observed_at,
            "observed_run_id": product.last_observed_run_id,
        })
    return products


async def _cached_provenance(
    db: AsyncSession, competitor: Competitor, products: list[dict]
) -> CollectionExportProvenance:
    runs = (
        await db.execute(select(ScrapeRun).where(ScrapeRun.competitor_id == competitor.id))
    ).scalars().all()
    terminal_runs = [run for run in runs if run.status in {"success", "failed", "abandoned", "stale_skipped"}]
    latest_terminal = max(terminal_runs, key=_run_sort_key, default=None)
    complete_runs = [
        run for run in terminal_runs
        if run.status == "success" and run.completeness == "complete"
    ]
    latest_complete = max(complete_runs, key=_run_sort_key, default=None)
    evidence = CompetitorEvidence(
        latest_complete=_run_evidence(latest_complete),
        latest_terminal=_run_evidence(latest_terminal),
    )
    coverage_state = assess_catalog_coverage(evidence, now=datetime.now(timezone.utc))
    observed_at = [item["observed_at"] for item in products if isinstance(item.get("observed_at"), datetime)]
    latest_complete_id = latest_complete.id if latest_complete else None
    degraded_rows = sum(
        item.get("observed_run_id") != latest_complete_id
        for item in products
    ) if latest_complete_id is not None else len(products)
    return CollectionExportProvenance(
        requested_mode="cached",
        source="cached",
        completeness="unknown" if latest_terminal is None else _export_completeness(latest_terminal.completeness),
        products_count=len(products),
        safe_reason=(
            "Stored rows may span observations and are not a live collection acquisition."
            if products else "No active stored products match this collection."
        ),
        cached_coverage_basis="active stored products matched by current collection aliases; row observations may span runs",
        coverage_state=coverage_state,
        newest_observed_at=max(observed_at, default=None),
        oldest_observed_at=min(observed_at, default=None),
        latest_complete_run_id=latest_complete_id,
        latest_complete_at=(latest_complete.observation_completed_at if latest_complete else None),
        latest_terminal_run_id=(latest_terminal.id if latest_terminal else None),
        degraded_or_legacy_row_count=degraded_rows,
    )


def _live_provenance(result: AcquisitionResult, *, products_count: int) -> CollectionExportProvenance:
    return CollectionExportProvenance(
        requested_mode="live",
        source="live",
        completeness=_export_completeness(result.completeness),
        products_count=products_count,
        pages_fetched=result.pages_fetched,
        page_cap_reached=result.page_cap_reached,
        acquisition_started_at=result.started_at,
        acquisition_completed_at=result.completed_at,
        observation_started_at=result.observation_started_at,
        observation_completed_at=result.observation_completed_at,
        safe_reason=result.completeness_reason,
    )


def _live_coverage_state(result: AcquisitionResult) -> str:
    if result.completeness == "complete":
        return "current_complete"
    return result.completeness


def _export_completeness(value: str | None) -> Literal["complete", "partial", "suspicious_empty", "failed", "unknown"]:
    if value in {"complete", "partial", "suspicious_empty", "failed"}:
        return value
    return "unknown"


def _run_evidence(run: ScrapeRun | None) -> RunEvidence | None:
    if run is None:
        return None
    return RunEvidence(
        run_id=run.id,
        status=run.status,
        completeness=run.completeness,
        observation_completed_at=run.observation_completed_at,
        terminal_at=run.terminal_at,
        failure_category=run.failure_category,
        failure_reason=run.error_message,
    )


def _run_sort_key(run: ScrapeRun) -> tuple[datetime, int]:
    timestamp = run.terminal_at or run.finished_at or run.observation_completed_at
    return (timestamp or datetime.min.replace(tzinfo=timezone.utc), run.id)


def _provenance_headers(provenance: CollectionExportProvenance) -> dict[str, str]:
    values = {
        "Provenance-Version": "1",
        "Requested-Mode": provenance.requested_mode,
        "Source": provenance.source,
        "Completeness": provenance.completeness,
        "Products-Count": str(provenance.products_count),
        "Pages-Fetched": str(provenance.pages_fetched),
        "Page-Cap-Reached": str(provenance.page_cap_reached).lower(),
        "Safe-Reason": provenance.safe_reason or "",
        "Coverage-State": provenance.coverage_state or "",
        "Cached-Coverage-Basis": provenance.cached_coverage_basis or "",
        "Newest-Observed-At": _header_datetime(provenance.newest_observed_at),
        "Oldest-Observed-At": _header_datetime(provenance.oldest_observed_at),
        "Latest-Complete-Run-Id": _header_int(provenance.latest_complete_run_id),
        "Latest-Complete-At": _header_datetime(provenance.latest_complete_at),
        "Latest-Terminal-Run-Id": _header_int(provenance.latest_terminal_run_id),
        "Degraded-Or-Legacy-Row-Count": str(provenance.degraded_or_legacy_row_count),
        "Acquisition-Started-At": _header_datetime(provenance.acquisition_started_at),
        "Acquisition-Completed-At": _header_datetime(provenance.acquisition_completed_at),
        "Observation-Started-At": _header_datetime(provenance.observation_started_at),
        "Observation-Completed-At": _header_datetime(provenance.observation_completed_at),
    }
    return {f"{EXPORT_HEADER_PREFIX}{key}": value for key, value in values.items()}


def _header_datetime(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _header_int(value: int | None) -> str:
    return str(value) if value is not None else ""


def _product_matches_collection(product: Product, collection_url: str) -> bool:
    aliases = _collection_aliases(collection_url)
    category = normalize_title(product.category or "")
    title = normalize_title(product.title or "")
    haystack = f"{category} {title}".strip()
    return any(_collection_alias_matches(alias, category, haystack) for alias in aliases)


def _collection_aliases(collection_url: str) -> list[str]:
    handle = _collection_handle(collection_url) or ""
    normalized = normalize_title(handle.replace("-", " "))
    aliases = [normalized]
    if "brainrot" in normalized:
        aliases.extend(["brainrot", "brainrots", "sab"])
    if "murder mystery" in normalized or normalized == "mm2":
        aliases.extend(["murder mystery 2", "mm2"])
    if "grow a garden" in normalized or normalized in {"gag", "gag2"}:
        aliases.extend(["grow a garden", "grow a garden 2", "gag", "gag2"])
    if "adopt me" in normalized or normalized == "adm":
        aliases.extend(["adopt me", "adm"])
    if "blox fruit" in normalized:
        aliases.extend(["blox fruits", "blox fruit"])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _collection_alias_matches(alias: str, category: str, haystack: str) -> bool:
    if not alias:
        return False
    if alias == category:
        return True
    if f" {alias} " in f" {haystack} ":
        return True
    compact_alias = alias.replace(" ", "")
    compact_category = category.replace(" ", "")
    return bool(compact_alias and compact_alias == compact_category)


def _csv_body(rows: list[dict], *, include_provenance: bool = False) -> str:
    output = io.StringIO()
    fields = EXPORT_FIELDS + (EXPORT_PROVENANCE_FIELDS if include_provenance else [])
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _validate_collection_url(competitor: Competitor, collection_url: str) -> None:
    parsed = urlparse(collection_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Collection URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Collection URL must not include credentials")

    try:
        competitor_host = _normalized_host(competitor.base_url)
        collection_host = _normalized_host(collection_url)
        same_port = _effective_port(competitor.base_url) == _effective_port(collection_url)
    except ValueError:
        raise HTTPException(status_code=400, detail="Collection URL must use a valid host and port")
    if not collection_host or collection_host != competitor_host:
        raise HTTPException(status_code=400, detail="Collection URL must belong to the selected competitor")
    if not same_port:
        raise HTTPException(status_code=400, detail="Collection URL must use the selected competitor host and port")
    if _is_private_or_local_host(collection_host):
        raise HTTPException(status_code=400, detail="Collection URL must not target a local or private network host")


def _normalized_host(value: str) -> str:
    return (urlparse(value).hostname or "").lower().removeprefix("www.").rstrip(".")


def _effective_port(value: str) -> int | None:
    parsed = urlparse(value)
    if parsed.port is not None:
        return parsed.port
    return {"http": 80, "https": 443}.get(parsed.scheme)


def _is_private_or_local_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified)


def _collection_handle(collection_url: str) -> Optional[str]:
    match = re.search(r"/collections/([^/?#]+)", collection_url or "")
    return match.group(1) if match else None


def _export_filename(competitor_name: str, collection_url: str, format: str) -> str:
    handle = _collection_handle(collection_url) or "collection"
    extension = "jsonl" if format == "jsonl" else format
    safe_name = re.sub(r"[^a-z0-9]+", "-", competitor_name.lower()).strip("-") or "competitor"
    safe_handle = re.sub(r"[^a-z0-9]+", "-", handle.lower()).strip("-") or "collection"
    return f"{safe_name}-{safe_handle}-prices.{extension}"
