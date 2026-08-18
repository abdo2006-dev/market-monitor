from pydantic import BaseModel, Field, HttpUrl, field_validator
from typing import Optional, List, Any, Literal
from datetime import datetime
from decimal import Decimal
from uuid import UUID


# ── Competitor ──────────────────────────────────────────────────────────────

class CompetitorBase(BaseModel):
    name: str
    base_url: str
    category: Optional[str] = None
    active: bool = True
    scan_frequency_minutes: int = 60
    scrape_type: str = "generic_selector"
    listing_urls: List[str] = []
    selector_config: dict = {}
    discord_webhook_url: Optional[str] = None
    notes: Optional[str] = None


class CompetitorCreate(CompetitorBase):
    pass


class CompetitorUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None
    scan_frequency_minutes: Optional[int] = None
    scrape_type: Optional[str] = None
    listing_urls: Optional[List[str]] = None
    selector_config: Optional[dict] = None
    discord_webhook_url: Optional[str] = None
    notes: Optional[str] = None


class CompetitorOut(CompetitorBase):
    id: int
    last_scan_at: Optional[datetime] = None
    last_scan_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Product ──────────────────────────────────────────────────────────────────

class ProductOut(BaseModel):
    id: int
    competitor_id: int
    competitor_name: Optional[str] = None
    external_id: Optional[str] = None
    title: str
    normalized_title: str
    category: Optional[str] = None
    url: str
    image_url: Optional[str] = None
    current_price: Optional[Decimal] = None
    currency: str
    stock_status: str
    sku: Optional[str] = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_checked_at: datetime
    last_observed_at: Optional[datetime] = None
    last_observed_run_id: Optional[int] = None
    active: bool

    class Config:
        from_attributes = True


class ProductDetailOut(ProductOut):
    pass


class SnapshotOut(BaseModel):
    id: int
    product_id: int
    title: str
    price: Optional[Decimal] = None
    currency: str
    stock_status: str
    image_url: Optional[str] = None
    checked_at: datetime
    observed_at: Optional[datetime] = None
    scrape_run_id: Optional[int] = None

    class Config:
        from_attributes = True


# ── Event ────────────────────────────────────────────────────────────────────

class EventOut(BaseModel):
    id: int
    competitor_id: int
    competitor_name: Optional[str] = None
    product_id: Optional[int] = None
    product_title: Optional[str] = None
    product_category: Optional[str] = None
    scrape_run_id: Optional[int] = None
    event_type: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    event_message: Optional[str] = None
    detected_at: datetime
    notification_sent: bool
    notification_sent_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Scrape Run ────────────────────────────────────────────────────────────────

class ScrapeRunOut(BaseModel):
    id: int
    competitor_id: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    products_found: int
    new_products_count: int
    price_changes_count: int
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class SyncRunStatus(BaseModel):
    run_id: int
    competitor_id: int
    competitor_name: Optional[str] = None
    status: str
    trigger: str
    queued_at: datetime
    started_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    acquisition_started_at: Optional[datetime] = None
    acquisition_completed_at: Optional[datetime] = None
    observation_started_at: Optional[datetime] = None
    observation_completed_at: Optional[datetime] = None
    reconciled_at: Optional[datetime] = None
    terminal_at: Optional[datetime] = None
    attempt: int
    max_attempts: int
    next_attempt_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    failure_category: Optional[str] = None
    failure_reason: Optional[str] = None
    products_observed: int
    pages_fetched: int
    request_count: int
    page_cap_reached: bool
    acquisition_strategy: Optional[str] = None
    completeness: str
    completeness_reason: Optional[str] = None
    duration_seconds: Optional[float] = None
    queue_latency_seconds: Optional[float] = None
    acquisition_duration_seconds: Optional[float] = None
    reconciliation_duration_seconds: Optional[float] = None
    queue_age_seconds: float
    operator_state: str


class SyncRequestStatus(BaseModel):
    request_id: UUID
    trigger: str
    status: str
    requested_at: datetime
    dispatch_status: str
    dispatch_error_category: Optional[str] = None
    runner_state: str
    needs_runner_recovery: bool
    oldest_queued_seconds: Optional[float] = None
    runs: List[SyncRunStatus]


class CompetitorFreshness(BaseModel):
    competitor_id: int
    competitor_name: str
    coverage_complete: bool
    last_complete_at: Optional[datetime] = None
    latest_partial_at: Optional[datetime] = None
    last_failed_at: Optional[datetime] = None
    active_products: int = 0
    last_complete_run: Optional[SyncRunStatus] = None
    active_run: Optional[SyncRunStatus] = None


# ── Market Search ────────────────────────────────────────────────────────────

class SearchSuggestionPrice(BaseModel):
    currency: str
    lowest_observed_price: Optional[float] = None


class SearchSuggestionOut(BaseModel):
    title: str
    normalized_title: str
    base_title: str
    base_normalized_title: str
    mutation: str
    mutation_label: str
    category: Optional[str] = None
    representative_product_id: int
    best_price: Optional[float] = None
    currency: str
    image_url: Optional[str] = None
    competitors: List[str]
    competitors_count: int
    variants: List[str]
    match_score: float
    prices_by_currency: List[SearchSuggestionPrice] = Field(default_factory=list)


class SearchSuggestionsResponse(BaseModel):
    items: List[SearchSuggestionOut]
    total: int
    query: str
    candidates_considered: int
    candidate_limit_reached: bool


class SearchRunEvidence(BaseModel):
    run_id: int
    status: str
    completeness: str
    observation_completed_at: Optional[datetime] = None
    terminal_at: Optional[datetime] = None
    failure_category: Optional[str] = None
    failure_reason: Optional[str] = None


class SearchTrustMetadata(BaseModel):
    coverage_state: Literal[
        "current_complete", "partial", "suspicious_empty", "failed", "stale", "unknown"
    ]
    price_reliability: Literal["reliable", "degraded", "unknown", "unavailable"]
    reliable: bool
    trustworthy_current_observation: bool
    product_observed_at: Optional[datetime] = None
    product_observation_age_seconds: Optional[int] = None
    latest_complete_at: Optional[datetime] = None
    complete_coverage_age_seconds: Optional[int] = None
    latest_partial_at: Optional[datetime] = None
    last_failed_at: Optional[datetime] = None
    required_cycle_date: str
    current_completeness: str
    warning: Optional[str] = None
    producing_run: Optional[SearchRunEvidence] = None
    latest_complete_run: Optional[SearchRunEvidence] = None
    latest_partial_run: Optional[SearchRunEvidence] = None
    latest_failed_run: Optional[SearchRunEvidence] = None
    active_sync: Optional[SearchRunEvidence] = None


class SearchPriceChange(BaseModel):
    previous_price: float
    current_price: float
    currency: str
    direction: Literal["increase", "decrease"]
    amount: float
    percentage: Optional[float] = None
    changed_at: datetime
    scrape_run_id: Optional[int] = None


class SearchProductOut(ProductOut):
    """Search keeps the established JSON-number price contract."""

    current_price: Optional[float] = None


class SearchCompareRow(BaseModel):
    competitor_id: int
    competitor_name: str
    match_score: float
    product: Optional[SearchProductOut] = None
    trust: SearchTrustMetadata
    price_change: Optional[SearchPriceChange] = None
    reference_currency: Optional[str] = None
    difference_from_reliable_low: Optional[float] = None
    difference_percentage: Optional[float] = None


class SearchCurrencySummary(BaseModel):
    currency: str
    lowest_reliable_price: Optional[float] = None
    lowest_reliable_competitor_id: Optional[int] = None
    lowest_reliable_competitor_name: Optional[str] = None
    lowest_observed_price: Optional[float] = None
    lowest_observed_competitor_id: Optional[int] = None
    lowest_observed_competitor_name: Optional[str] = None
    highest_reliable_price: Optional[float] = None
    median_reliable_price: Optional[float] = None
    observed_price_count: int
    reliable_price_count: int


class SearchMarketSummary(BaseModel):
    currencies: List[SearchCurrencySummary]
    competitors_carrying: int
    trustworthy_current: int
    degraded_or_unknown: int
    syncing_competitors: int
    no_reliable_prices: bool


class SearchCompareResponse(BaseModel):
    target: Optional[SearchProductOut] = None
    identity: Optional[dict] = None
    aliases: Optional[List[str]] = None
    items: List[SearchCompareRow]
    total_matches: int
    market_summary: SearchMarketSummary


# ── Collection export ───────────────────────────────────────────────────────

class CollectionExportProvenance(BaseModel):
    """File-level evidence carried in export response headers.

    CSV and JSONL keep their established row schemas.  The browser reads this
    typed shape from ``X-Market-Monitor-Export-*`` headers, while callers that
    explicitly request ``include_provenance=true`` also receive it in the JSON
    envelope.
    """

    requested_mode: Literal["live", "cached"]
    source: Literal["live", "cached"]
    completeness: Literal["complete", "partial", "suspicious_empty", "failed", "unknown"]
    products_count: int
    pages_fetched: int = 0
    page_cap_reached: bool = False
    acquisition_started_at: Optional[datetime] = None
    acquisition_completed_at: Optional[datetime] = None
    observation_started_at: Optional[datetime] = None
    observation_completed_at: Optional[datetime] = None
    safe_reason: Optional[str] = None
    cached_coverage_basis: Optional[str] = None
    coverage_state: Optional[Literal[
        "current_complete", "partial", "suspicious_empty", "failed", "stale", "unknown"
    ]] = None
    newest_observed_at: Optional[datetime] = None
    oldest_observed_at: Optional[datetime] = None
    latest_complete_run_id: Optional[int] = None
    latest_complete_at: Optional[datetime] = None
    latest_terminal_run_id: Optional[int] = None
    degraded_or_legacy_row_count: int = 0


class CollectionExportFailure(BaseModel):
    code: Literal["live_acquisition_failed", "cached_export_unavailable"]
    message: str
    provenance: CollectionExportProvenance


# ── Settings ─────────────────────────────────────────────────────────────────

class AppSettingsOut(BaseModel):
    id: int
    default_scan_interval_minutes: int
    default_max_pages: int
    default_page_delay_seconds: float
    discord_notifications_enabled: bool
    daily_summary_enabled: bool
    daily_summary_time: str
    min_price_change_amount: Optional[Decimal] = None
    min_price_change_percentage: float
    ignore_keywords: str
    user_agent: str

    class Config:
        from_attributes = True


class AppSettingsUpdate(BaseModel):
    default_scan_interval_minutes: Optional[int] = None
    default_max_pages: Optional[int] = None
    default_page_delay_seconds: Optional[float] = None
    discord_notifications_enabled: Optional[bool] = None
    daily_summary_enabled: Optional[bool] = None
    daily_summary_time: Optional[str] = None
    min_price_change_amount: Optional[float] = None
    min_price_change_percentage: Optional[float] = None
    ignore_keywords: Optional[str] = None
    user_agent: Optional[str] = None


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DashboardSummary(BaseModel):
    new_products_today: int
    price_changes_today: int
    price_drops_today: int
    price_increases_today: int
    failed_scans_today: int
    latest_events: List[EventOut]
    competitors_needing_attention: List[CompetitorOut]


# ── Pagination ────────────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
