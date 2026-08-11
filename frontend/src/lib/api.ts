import axios from 'axios'
import type {
  BatchCompareSummaryResponse,
  CollectionExportParams,
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
  })
  return `${api.defaults.baseURL}/exports/collection-prices?${query.toString()}`
}

// Settings
export const getSettings = () => api.get('/settings').then(r => r.data)
export const updateSettings = (data: any) => api.put('/settings', data).then(r => r.data)

export default api
