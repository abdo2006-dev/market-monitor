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
  last_scan_status?: 'success' | 'failed' | null
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
}

export interface SearchSuggestionsResponse {
  items: SearchSuggestion[]
  total: number
  query: string
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

export interface ScanResult {
  status: 'success' | 'failed'
  products_found?: number
  new_products?: number
  price_changes?: number
  error?: string
}

/**
 * POST /api/competitors/{id}/scan-now returns ONE OF TWO SHAPES depending on
 * the competitor's scrape_type, and `result` may be null for a scan that never
 * ran (inactive competitor). This union documents the real contract rather than
 * hiding it — see docs/API_CONTRACTS.md §2.1 and ADR 0003, which replaces both
 * shapes with a single durable scan-run response.
 */
export type ScanNowResponse =
  | { message: string; result: ScanResult | null; task_id?: undefined }
  | { message: string; task_id: string; result?: undefined }

/**
 * One entry in the client-side scan-all summary.
 *
 * BUG, CHARACTERISED (found by typing this file in Phase 1A):
 * `status` mixes two different vocabularies. The fan-out assigns
 * 'queued' | 'completed' | 'failed' | 'skipped', but api.ts:55 passes through
 * `result.result?.status` when present, which is a ScanResult status —
 * 'success' | 'failed'. So a successful inline scan yields the literal
 * 'success', which no consumer checks for.
 *
 * It happens to work: Competitors.tsx counts 'failed' and 'queued' explicitly
 * and derives `completed` by subtraction, so 'success' lands in the right
 * bucket by accident. Typed accurately here rather than fixed, because ADR 0003
 * replaces this whole client-side fan-out. Do not rely on the accident.
 */
export interface ScanAllItem {
  competitor_id: number
  name: string
  status: 'completed' | 'queued' | 'failed' | 'skipped' | 'success'
  result?: ScanNowResponse
  error?: string
}

export interface ScanAllSummary {
  message: string
  total: number
  completed: number
  queued: number
  failed: number
  items: ScanAllItem[]
}

// ── Exports ──────────────────────────────────────────────────────────────────

export type ExportFormat = 'csv' | 'jsonl' | 'json'

export interface CollectionExportParams {
  competitor_id: string | number
  collection_url: string
  format: ExportFormat | string
  max_pages: string | number
}

/**
 * Shape of the `json` export envelope.
 *
 * NOTE: there is deliberately NO provenance field here, because the backend does
 * not send one. When the live scrape returns nothing, the endpoint silently
 * serves stored products in this identical shape. Phase 1D adds a provenance
 * block — see docs/DAILY_CRITICAL_WORKFLOWS.md §3.1.
 */
export interface CollectionExportEnvelope {
  competitor: string
  collection_url: string
  products_count: number
  items: CollectionExportRow[]
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
}
