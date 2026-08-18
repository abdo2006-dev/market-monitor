"""Pure Search trust rules built from durable Sync evidence.

Search deliberately keeps the observed price visible even when this module does not
consider it reliable enough for the current market range.  The rule is based on the
daily Cairo Sync cycle and durable catalog completeness, never on request/commit time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
from app.domain.market_cycle import CAIRO, aware_utc, required_market_cycle_date

CoverageState = Literal[
    "current_complete",
    "partial",
    "suspicious_empty",
    "failed",
    "stale",
    "unknown",
]
PriceReliability = Literal["reliable", "degraded", "unknown", "unavailable"]


@dataclass(frozen=True)
class RunEvidence:
    run_id: int
    status: str
    completeness: str
    observation_completed_at: datetime | None = None
    terminal_at: datetime | None = None
    failure_category: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class CompetitorEvidence:
    latest_complete: RunEvidence | None = None
    latest_partial: RunEvidence | None = None
    latest_failed: RunEvidence | None = None
    latest_terminal: RunEvidence | None = None
    active_run: RunEvidence | None = None


@dataclass(frozen=True)
class TrustAssessment:
    coverage_state: CoverageState
    price_reliability: PriceReliability
    reliable: bool
    trustworthy_current_observation: bool
    required_cycle_date: date
    product_observation_age_seconds: int | None
    complete_coverage_age_seconds: int | None
    warning: str | None


def required_cairo_cycle_date(now: datetime) -> date:
    """Compatibility name for the shared market-cycle policy."""
    return required_market_cycle_date(now)


def assess_catalog_coverage(
    evidence: CompetitorEvidence, *, now: datetime
) -> CoverageState:
    """Classify durable catalog coverage without making a product-price claim.

    Export uses this same vocabulary for stored data.  It deliberately answers
    only what the latest catalog attempt proves; price reliability remains a
    Search-specific product-level decision.
    """
    return _coverage_state(evidence, required_market_cycle_date(now))


def assess_search_trust(
    *,
    evidence: CompetitorEvidence,
    product_observed_at: datetime | None,
    product_run_id: int | None,
    has_price: bool,
    stock_status: str | None,
    now: datetime,
) -> TrustAssessment:
    now_utc = aware_utc(now)
    required_cycle = required_cairo_cycle_date(now_utc)
    coverage_state = assess_catalog_coverage(evidence, now=now_utc)
    latest_complete = evidence.latest_complete
    directly_observed_in_complete = bool(
        coverage_state == "current_complete"
        and latest_complete
        and product_run_id == latest_complete.run_id
        and product_observed_at is not None
    )
    reliable = bool(
        directly_observed_in_complete
        and has_price
        and stock_status == "in_stock"
    )

    if not has_price:
        price_reliability: PriceReliability = "unavailable"
    elif reliable:
        price_reliability = "reliable"
    elif coverage_state == "unknown" or product_observed_at is None or product_run_id is None:
        price_reliability = "unknown"
    else:
        price_reliability = "degraded"

    warning = _warning(
        coverage_state=coverage_state,
        directly_observed_in_complete=directly_observed_in_complete,
        has_price=has_price,
        stock_status=stock_status,
    )
    complete_at = latest_complete.observation_completed_at if latest_complete else None
    return TrustAssessment(
        coverage_state=coverage_state,
        price_reliability=price_reliability,
        reliable=reliable,
        trustworthy_current_observation=directly_observed_in_complete,
        required_cycle_date=required_cycle,
        product_observation_age_seconds=_age_seconds(product_observed_at, now_utc),
        complete_coverage_age_seconds=_age_seconds(complete_at, now_utc),
        warning=warning,
    )


def _coverage_state(evidence: CompetitorEvidence, required_cycle: date) -> CoverageState:
    latest = evidence.latest_terminal
    if latest is None:
        return "unknown"
    if latest.status in {"failed", "abandoned"}:
        return "failed"
    if latest.status != "success":
        return "unknown"
    if latest.completeness == "partial":
        return "partial"
    if latest.completeness == "suspicious_empty":
        return "suspicious_empty"
    if latest.completeness != "complete" or latest.observation_completed_at is None:
        return "unknown"
    observed_date = aware_utc(latest.observation_completed_at).astimezone(CAIRO).date()
    return "current_complete" if observed_date >= required_cycle else "stale"


def _warning(
    *,
    coverage_state: CoverageState,
    directly_observed_in_complete: bool,
    has_price: bool,
    stock_status: str | None,
) -> str | None:
    if coverage_state == "partial":
        return "Latest catalog observation was partial; this price is excluded from the reliable range."
    if coverage_state == "suspicious_empty":
        return "Latest catalog observation was suspiciously empty; prior prices remain visible but degraded."
    if coverage_state == "failed":
        return "Latest Sync attempt failed; this is the last stored observation, not a confirmed current price."
    if coverage_state == "stale":
        return "Complete coverage is from an earlier Cairo Sync cycle."
    if coverage_state == "unknown":
        return "No trustworthy V2 catalog lineage is available for this stored price."
    if not directly_observed_in_complete:
        return "This product was not observed in the latest complete catalog."
    if not has_price:
        return "No comparable price is stored for this product."
    if stock_status != "in_stock":
        return "The observed price is excluded from the reliable range because the item is not confirmed in stock."
    return None


def _age_seconds(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, int((now - aware_utc(value)).total_seconds()))
