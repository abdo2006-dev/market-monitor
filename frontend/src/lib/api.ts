import axios from 'axios'
import type {
  BatchCompareSummaryResponse,
  CollectionExportDownload,
  CollectionExportParams,
  CollectionExportProvenance,
  CompareResponse,
  Competitor,
  CompetitorInput,
  ScanAllSummary,
  ScanNowResponse,
  CompetitorFreshness,
  SyncRequestStatus,
  SyncRunStatus,
  SearchSuggestionsResponse,
} from './types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
})

// Competitors
export const getCompetitors = (): Promise<Competitor[]> =>
  api.get('/competitors').then(r => r.data)
export const createCompetitor = (data: CompetitorInput): Promise<Competitor> =>
  api.post('/competitors', data).then(r => r.data)
export const seedDefaultCompetitors = (): Promise<Competitor[]> =>
  api.post('/competitors/seed-defaults').then(r => r.data)
export const updateCompetitor = (id: number, data: Partial<CompetitorInput>): Promise<Competitor> =>
  api.put(`/competitors/${id}`, data).then(r => r.data)
export const deleteCompetitor = (id: number) => api.delete(`/competitors/${id}`)
export const scanNow = (id: number): Promise<ScanNowResponse> =>
  api.post(`/sync/competitors/${id}`).then(r => r.data)

/** One durable server-owned Sync All request; the browser never fans out work. */
export const scanAllCompetitors = (): Promise<ScanAllSummary> =>
  api.post('/sync/all').then(r => r.data)
export const getSyncRequest = (requestId: string): Promise<SyncRequestStatus> =>
  api.get(`/sync/requests/${requestId}`).then(r => r.data)
export const getSyncRun = (runId: number): Promise<SyncRunStatus> =>
  api.get(`/sync/runs/${runId}`).then(r => r.data)
export const getSyncFreshness = (): Promise<CompetitorFreshness[]> =>
  api.get('/sync/freshness').then(r => r.data)
export const getCompetitor = (id: number) => api.get(`/competitors/${id}`).then(r => r.data)

// Products
export const getProducts = (params: any) => api.get('/products', { params }).then(r => r.data)
export const getProduct = (id: number) => api.get(`/products/${id}`).then(r => r.data)
export const getProductHistory = (id: number) => api.get(`/products/${id}/history`).then(r => r.data)

// Events
export const getEvents = (params: any) => api.get('/events', { params }).then(r => r.data)

// Search
export const searchProducts = (params: any) => api.get('/search/products', { params }).then(r => r.data)

export const getSearchSuggestions = (
  params: { q: string; limit?: number },
): Promise<SearchSuggestionsResponse> =>
  api.get('/search/suggestions', { params }).then(r => r.data)

/** Pass `product_id` for an exact target, or `q` to let the backend pick one. */
export const compareProduct = (
  params: { product_id: number; q?: never } | { q: string; product_id?: never },
): Promise<CompareResponse> =>
  api.get('/search/compare', { params }).then(r => r.data)

export const batchCompareSummary = (
  params: { queries: string[]; format?: 'json' },
): Promise<BatchCompareSummaryResponse> =>
  api.get('/search/batch-compare-summary', { params }).then(r => r.data)

// Dashboard
export const getDashboardSummary = () => api.get('/dashboard/summary').then(r => r.data)
export const getSalesTrends = (params: any) => api.get('/dashboard/sales-trends', { params }).then(r => r.data)

// Exports
export const collectionPricesExportUrl = (params: CollectionExportParams): string => {
  const query = new URLSearchParams({
    competitor_id: String(params.competitor_id),
    collection_url: params.collection_url,
    format: params.format,
    max_pages: String(params.max_pages),
    mode: params.mode || 'live',
  })
  if (params.include_provenance) query.set('include_provenance', 'true')
  return `${api.defaults.baseURL}/exports/collection-prices?${query.toString()}`
}

const exportHeader = (headers: Record<string, unknown>, name: string): string | null => {
  const value = headers[`x-market-monitor-export-${name}`]
  return typeof value === 'string' && value.length > 0 ? value : null
}

const exportNumber = (value: string | null): number | null => {
  if (value === null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const exportDate = (value: string | null): string | null => value || null

export const parseCollectionExportProvenance = (
  headers: Record<string, unknown>,
): CollectionExportProvenance => ({
  requested_mode: (exportHeader(headers, 'requested-mode') || 'live') as CollectionExportProvenance['requested_mode'],
  source: (exportHeader(headers, 'source') || 'live') as CollectionExportProvenance['source'],
  completeness: (exportHeader(headers, 'completeness') || 'unknown') as CollectionExportProvenance['completeness'],
  products_count: exportNumber(exportHeader(headers, 'products-count')) || 0,
  pages_fetched: exportNumber(exportHeader(headers, 'pages-fetched')) || 0,
  page_cap_reached: exportHeader(headers, 'page-cap-reached') === 'true',
  acquisition_started_at: exportDate(exportHeader(headers, 'acquisition-started-at')),
  acquisition_completed_at: exportDate(exportHeader(headers, 'acquisition-completed-at')),
  observation_started_at: exportDate(exportHeader(headers, 'observation-started-at')),
  observation_completed_at: exportDate(exportHeader(headers, 'observation-completed-at')),
  safe_reason: exportHeader(headers, 'safe-reason'),
  cached_coverage_basis: exportHeader(headers, 'cached-coverage-basis'),
  coverage_state: exportHeader(headers, 'coverage-state') as CollectionExportProvenance['coverage_state'],
  newest_observed_at: exportDate(exportHeader(headers, 'newest-observed-at')),
  oldest_observed_at: exportDate(exportHeader(headers, 'oldest-observed-at')),
  latest_complete_run_id: exportNumber(exportHeader(headers, 'latest-complete-run-id')),
  latest_complete_at: exportDate(exportHeader(headers, 'latest-complete-at')),
  latest_terminal_run_id: exportNumber(exportHeader(headers, 'latest-terminal-run-id')),
  degraded_or_legacy_row_count: exportNumber(exportHeader(headers, 'degraded-or-legacy-row-count')) || 0,
})

const exportFilename = (contentDisposition: unknown, fallback: string): string => {
  if (typeof contentDisposition !== 'string') return fallback
  const match = /filename="?([^";]+)"?/i.exec(contentDisposition)
  return match?.[1] || fallback
}

/** Acquire/download bytes synchronously, then let the page present truthful evidence. */
export const prepareCollectionExport = async (
  params: CollectionExportParams,
): Promise<CollectionExportDownload> => {
  const response = await api.get<Blob>('/exports/collection-prices', {
    params: { ...params, mode: params.mode || 'live' },
    responseType: 'blob',
  })
  const extension = params.format === 'jsonl' ? 'jsonl' : params.format
  return {
    blob: response.data,
    filename: exportFilename(response.headers['content-disposition'], `collection-prices.${extension}`),
    provenance: parseCollectionExportProvenance(response.headers),
  }
}

// Settings
export const getSettings = () => api.get('/settings').then(r => r.data)
export const updateSettings = (data: any) => api.put('/settings', data).then(r => r.data)

export default api
