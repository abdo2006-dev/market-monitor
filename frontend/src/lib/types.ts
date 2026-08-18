/**
 * API response types for the daily critical path: Search, Exports, Sync.
 *
 * HAND-WRITTEN, DELIBERATELY. These are not generated yet, because 14 of 26
 * backend routes declare no `response_model` — including every Search endpoint —
 * so FastAPI's OpenAPI schema currently describes them as untyped objects.
 * Generating from it today would produce `unknown` for exactly the endpoints
 * that matter. See docs/API_CONTRACTS.md §5 for the Phase 1B plan that replaces
 * this file with generated types.
 *
 * Every shape here is verified against the backend contract tests in
 * backend/tests/critical/. If you change one, change the test too.
 */

// ── Shared ───────────────────────────────────────────────────────────────────

/** Prices cross the wire as JSON numbers, but may be absent. */
export type Price = number | null

/** ISO-8601 timestamp string. */
export type Timestamp = string

export type StockStatus = 'in_stock' | 'out_of_stock' | 'unknown'

/** Mirrors backend ProductOut (app/schemas/__init__.py). */
export interface Product {
  id: number
  competitor_id: number
  competitor_name?: string | null
  external_id?: string | null
  title: string
  normalized_title: string
  category?: string | null
  url: string
  image_url?: string | null
  current_price: Price
  currency: string
  stock_status: StockStatus | string
  sku?: string | null
  first_seen_at: Timestamp
  last_seen_at: Timestamp
  /** Freshness anchor. See docs/DAILY_CRITICAL_WORKFLOWS.md §5. */
  last_checked_at: Timestamp
  /** Actual external observation time and the V2 run that supplied it. */
  last_observed_at?: Timestamp | null
  last_observed_run_id?: number | null
  active: boolean
}

export interface Competitor {
  id: number
  name: string
  base_url: string
  category?: string | null
  active: boolean
  scan_frequency_minutes: number
  scrape_type: string
  listing_urls: string[]
  selector_config: Record<string, unknown>
  discord_webhook_url?: string | null
  notes?: string | null
  last_scan_at?: Timestamp | null
  last_scan_status?: 'success' | 'partial' | 'suspicious_empty' | 'failed' | null
  created_at: Timestamp
  updated_at: Timestamp
}

export interface CompetitorInput {
  name: string
  base_url: string
  category?: string | null
  active?: boolean
  scan_frequency_minutes?: number
  scrape_type?: string
  listing_urls?: string[]
  selector_config?: Record<string, unknown>
  discord_webhook_url?: string | null
  notes?: string | null
}

// ── Search ───────────────────────────────────────────────────────────────────

/** One grouped market item from GET /api/search/suggestions. */
export interface SearchSuggestion {
  title: string
  normalized_title: string
  base_title: string
  base_normalized_title: string
  /** 'normal' when the item has no variant/mutation. */
  mutation: string
  mutation_label: string
  category?: string | null
  representative_product_id: number
  best_price: Price
  currency: string
  image_url?: string | null
  competitors: string[]
  competitors_count: number
  variants: string[]
  match_score: number
  prices_by_currency: Array<{
    currency: string
    lowest_observed_price: Price
  }>
}

export interface SearchSuggestionsResponse {
  items: SearchSuggestion[]
  total: number
  query: string
  candidates_considered: number
  candidate_limit_reached: boolean
}

/**
 * One row of GET /api/search/compare.
 *
 * `product` is null for an active competitor with no match — the endpoint
 * returns full competitor coverage, not only hits.
 */
export interface CompareRow {
  competitor_id: number
  competitor_name: string
  match_score: number
  product: Product | null
  trust: SearchTrustMetadata
  price_change: SearchPriceChange | null
  reference_currency: string | null
  difference_from_reliable_low: Price
  difference_percentage: number | null
}

export type SearchCoverageState =
  | 'current_complete' | 'partial' | 'suspicious_empty'
  | 'failed' | 'stale' | 'unknown'

export interface SearchRunEvidence {
  run_id: number
  status: string
  completeness: AcquisitionCompleteness
  observation_completed_at: Timestamp | null
  terminal_at: Timestamp | null
  failure_category: string | null
  failure_reason: string | null
}

export interface SearchTrustMetadata {
  coverage_state: SearchCoverageState
  price_reliability: 'reliable' | 'degraded' | 'unknown' | 'unavailable'
  reliable: boolean
  trustworthy_current_observation: boolean
  product_observed_at: Timestamp | null
  product_observation_age_seconds: number | null
  latest_complete_at: Timestamp | null
  complete_coverage_age_seconds: number | null
  latest_partial_at: Timestamp | null
  last_failed_at: Timestamp | null
  required_cycle_date: string
  current_completeness: AcquisitionCompleteness
  warning: string | null
  producing_run: SearchRunEvidence | null
  latest_complete_run: SearchRunEvidence | null
  latest_partial_run: SearchRunEvidence | null
  latest_failed_run: SearchRunEvidence | null
  active_sync: SearchRunEvidence | null
}

export interface SearchPriceChange {
  previous_price: number
  current_price: number
  currency: string
  direction: 'increase' | 'decrease'
  amount: number
  percentage: number | null
  changed_at: Timestamp
  scrape_run_id: number | null
}

export interface SearchCurrencySummary {
  currency: string
  lowest_reliable_price: Price
  lowest_reliable_competitor_id: number | null
  lowest_reliable_competitor_name: string | null
  lowest_observed_price: Price
  lowest_observed_competitor_id: number | null
  lowest_observed_competitor_name: string | null
  highest_reliable_price: Price
  median_reliable_price: Price
  observed_price_count: number
  reliable_price_count: number
}

export interface SearchMarketSummary {
  currencies: SearchCurrencySummary[]
  competitors_carrying: number
  trustworthy_current: number
  degraded_or_unknown: number
  syncing_competitors: number
  no_reliable_prices: boolean
}

export interface MarketIdentity {
  key: string
  item_key: string
  collection: string
  collection_label: string
  base: string
  mutation: string
  mutation_label: string
  display_title: string
}

export interface CompareResponse {
  target: Product | null
  identity?: MarketIdentity
  aliases?: string[]
  items: CompareRow[]
  total_matches: number
  market_summary: SearchMarketSummary
}

export interface BatchCompareCompetitorPrice {
  competitor: string
  item: string
  category?: string | null
  price: Price
  currency: string
  url: string
  match_score: number
}

export interface BatchCompareSummaryItem {
  query: string
  matched_item: string | null
  category?: string | null
  lowest_price: Price
  lowest_seller: string | null
  currency?: string | null
  total_matches: number
  competitor_prices: BatchCompareCompetitorPrice[]
}

export interface BatchCompareSummaryResponse {
  items: BatchCompareSummaryItem[]
  total: number
}

// ── Sync ─────────────────────────────────────────────────────────────────────

export type SyncRunState =
  | 'queued' | 'running' | 'retry_wait' | 'success' | 'failed'
  | 'abandoned' | 'stale_skipped'
export type AcquisitionCompleteness =
  | 'unknown' | 'complete' | 'partial' | 'suspicious_empty' | 'failed'

export interface SyncRunStatus {
  run_id: number
  competitor_id: number
  competitor_name?: string | null
  status: SyncRunState
  trigger: string
  queued_at: Timestamp
  started_at?: Timestamp | null
  acquisition_started_at?: Timestamp | null
  acquisition_completed_at?: Timestamp | null
  reconciled_at?: Timestamp | null
  terminal_at?: Timestamp | null
  attempt: number
  max_attempts: number
  next_attempt_at?: Timestamp | null
  lease_expires_at?: Timestamp | null
  failure_category?: string | null
  failure_reason?: string | null
  products_observed: number
  pages_fetched: number
  page_cap_reached: boolean
  acquisition_strategy?: string | null
  completeness: AcquisitionCompleteness
  completeness_reason?: string | null
  duration_seconds?: number | null
}

export interface SyncRequestStatus {
  request_id: string
  trigger: string
  status: 'queued' | 'running' | 'retrying' | 'success' | 'partial' | 'failed'
  requested_at: Timestamp
  dispatch_status: 'not_requested' | 'dispatched' | 'failed'
  dispatch_error_category?: string | null
  runs: SyncRunStatus[]
}

export interface CompetitorFreshness {
  competitor_id: number
  competitor_name: string
  coverage_complete: boolean
  last_complete_at?: Timestamp | null
  latest_partial_at?: Timestamp | null
  last_failed_at?: Timestamp | null
  active_run?: SyncRunStatus | null
}

/** Compatibility alias for callers of the legacy route name. */
export type ScanNowResponse = SyncRequestStatus

/** Compatibility alias for callers of the legacy scan-all route name. */
export type ScanAllSummary = SyncRequestStatus

// ── Exports ──────────────────────────────────────────────────────────────────

export type ExportFormat = 'csv' | 'jsonl' | 'json'
export type ExportMode = 'live' | 'cached'
export type ExportCompleteness = 'complete' | 'partial' | 'suspicious_empty' | 'failed' | 'unknown'

export interface CollectionExportParams {
  competitor_id: string | number
  collection_url: string
  format: ExportFormat
  max_pages: string | number
  mode?: ExportMode
  include_provenance?: boolean
}

/** File-level acquisition/storage evidence returned in response headers. */
export interface CollectionExportProvenance {
  requested_mode: ExportMode
  source: ExportMode
  completeness: ExportCompleteness
  products_count: number
  pages_fetched: number
  page_cap_reached: boolean
  acquisition_started_at: Timestamp | null
  acquisition_completed_at: Timestamp | null
  observation_started_at: Timestamp | null
  observation_completed_at: Timestamp | null
  safe_reason: string | null
  cached_coverage_basis: string | null
  coverage_state: SearchCoverageState | null
  newest_observed_at: Timestamp | null
  oldest_observed_at: Timestamp | null
  latest_complete_run_id: number | null
  latest_complete_at: Timestamp | null
  latest_terminal_run_id: number | null
  degraded_or_legacy_row_count: number
}

export interface CollectionExportDownload {
  blob: Blob
  filename: string
  provenance: CollectionExportProvenance
}

export interface CollectionExportFailure {
  code: 'live_acquisition_failed' | 'cached_export_unavailable'
  message: string
  provenance: CollectionExportProvenance
}

/** The legacy JSON envelope remains compatible; provenance is opt-in. */
export interface CollectionExportEnvelope {
  competitor: string
  collection_url: string
  products_count: number
  items: CollectionExportRow[]
  provenance?: CollectionExportProvenance
}

export interface CollectionExportRow {
  competitor_name: string
  competitor_base_url: string
  collection_url: string
  category: string | null
  title: string
  price: Price
  currency: string
  stock_status: string
  sku: string | null
  external_id: string | null
  product_url: string
  image_url: string | null
  /** When the FILE was generated - not when the price was observed. */
  scraped_at: Timestamp
  /** Present only when `include_provenance=true`. */
  observed_at?: Timestamp | null
  observed_run_id?: number | null
  coverage_state?: SearchCoverageState | null
}
export type AuthStatus = {
  enabled: boolean
  authenticated: boolean
}
