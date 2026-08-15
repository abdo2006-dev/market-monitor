"""Canonical acquisition boundary for durable Sync.

Execution outcome and catalog completeness are deliberately independent: a
request can execute successfully while producing only partial evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


Completeness = Literal["complete", "partial", "suspicious_empty", "failed"]

# Scrapers use this reserved key for a server-clock timestamp captured at the
# response/page boundary. acquire_catalog removes it before observations cross
# the domain boundary, and deliberately ignores any storefront-supplied
# ``observed_at`` field.
INTERNAL_OBSERVED_AT_KEY = "_market_monitor_observed_at"


class AcquisitionFailure(RuntimeError):
    def __init__(self, category: str, safe_message: str, retryable: bool):
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message
        self.retryable = retryable


@dataclass(frozen=True)
class AcquisitionResult:
    observations: list[dict]
    strategy: str
    started_at: datetime
    completed_at: datetime
    pages_fetched: int
    request_count: int
    completeness: Completeness
    page_cap_reached: bool = False
    completeness_reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def product_count(self) -> int:
        return len(self.observations)

    @property
    def observation_started_at(self) -> datetime:
        timestamps = [
            item["observed_at"]
            for item in self.observations
            if isinstance(item.get("observed_at"), datetime)
        ]
        return min(timestamps, default=self.completed_at)

    @property
    def observation_completed_at(self) -> datetime:
        timestamps = [
            item["observed_at"]
            for item in self.observations
            if isinstance(item.get("observed_at"), datetime)
        ]
        return max(timestamps, default=self.completed_at)


async def acquire_catalog(competitor: dict, **scrape_options) -> AcquisitionResult:
    """Run the existing adapters and enrich their output with coverage evidence."""
    started_at = datetime.now(timezone.utc)
    telemetry: dict = {}
    if _preview_fixture_enabled(competitor):
        from app.infrastructure.preview_demo import acquire_fixture_catalog

        observations, telemetry = acquire_fixture_catalog(competitor)
    else:
        from app.services.scraper import scrape_competitor

        observations = await scrape_competitor(
            competitor, telemetry=telemetry, **scrape_options
        )
    completed_at = datetime.now(timezone.utc)
    if not observations and telemetry.get("failure_category"):
        raise AcquisitionFailure(
            str(telemetry["failure_category"]),
            str(telemetry.get("failure_message") or "Catalog acquisition failed"),
            bool(telemetry.get("failure_retryable", True)),
        )
    normalized_observations = []
    for observation in observations:
        captured_at = observation.get(INTERNAL_OBSERVED_AT_KEY)
        if not (
            isinstance(captured_at, datetime)
            and captured_at.tzinfo is not None
            and started_at <= captured_at <= completed_at
        ):
            captured_at = completed_at
        payload = {
            key: value
            for key, value in observation.items()
            if key not in (INTERNAL_OBSERVED_AT_KEY, "observed_at")
        }
        normalized_observations.append({**payload, "observed_at": captured_at})
    observations = normalized_observations

    allow_empty = (competitor.get("selector_config") or {}).get("allow_empty_catalog") is True
    page_cap_reached = bool(telemetry.get("page_cap_reached"))
    if not observations and not allow_empty:
        completeness: Completeness = "suspicious_empty"
        reason = "Unexpected zero-product result; absence inference disabled"
    elif telemetry.get("failure_category"):
        completeness = "partial"
        reason = "One or more catalog source requests failed; absence inference disabled"
    elif page_cap_reached:
        completeness = "partial"
        reason = "Pagination cap reached while another page may exist"
    else:
        completeness = "complete"
        reason = "Adapter reached a catalog end signal"

    return AcquisitionResult(
        observations=observations,
        strategy=str(telemetry.get("strategy") or competitor.get("scrape_type") or "unknown"),
        started_at=started_at,
        completed_at=completed_at,
        pages_fetched=int(telemetry.get("pages_fetched") or 0),
        request_count=int(telemetry.get("request_count") or 0),
        completeness=completeness,
        page_cap_reached=page_cap_reached,
        completeness_reason=reason,
    )


def _preview_fixture_enabled(competitor: dict) -> bool:
    """Require both a preview deployment and an explicitly marked fixture row."""
    from app.config import settings

    selector_config = competitor.get("selector_config") or {}
    return (
        settings.PREVIEW_DEMO_MODE
        and settings.VERCEL_ENV == "preview"
        and selector_config.get("preview_demo") is True
    )
