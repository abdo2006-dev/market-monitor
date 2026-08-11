import axios from 'axios'
import type {
  BatchCompareSummaryResponse,
  CollectionExportParams,
  CompareResponse,
  Competitor,
  CompetitorInput,
  ScanAllItem,
  ScanAllSummary,
  ScanNowResponse,
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
  api.post(`/competitors/${id}/scan-now`).then(r => r.data)

/**
 * Client-side scan fan-out.
 *
 * NOTE: this is orchestration living in the transport layer, and it is scheduled
 * for removal. ADR 0003 moves eligibility, concurrency and result aggregation to
 * the backend so that closing this tab cannot abandon a bulk scan. Typed here
 * rather than redesigned, because Phase 1A does not migrate the scan pathway.
 */
export const scanAllCompetitors = async (
  competitors: Competitor[],
  concurrency = 4,
): Promise<ScanAllSummary> => {
  const activeCompetitors = competitors.filter(competitor => competitor.active)
  const items: ScanAllItem[] = []
  let cursor = 0

  async function worker() {
    while (cursor < activeCompetitors.length) {
      const competitor = activeCompetitors[cursor++]
      try {
        const result = await scanNow(competitor.id)
        items.push({
          competitor_id: competitor.id,
          name: competitor.name,
          status: result.result?.status || (result.task_id ? 'queued' : 'completed'),
          result,
        })
      } catch (error: any) {
        items.push({
          competitor_id: competitor.id,
          name: competitor.name,
          status: 'failed',
          error: error?.response?.data?.detail || error?.message || 'Scan failed',
        })
      }
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, activeCompetitors.length) }, () => worker())
  )

  const failed = items.filter(item => item.status === 'failed').length
  const queued = items.filter(item => item.status === 'queued').length
  const completed = items.length - failed - queued
  return {
    message: failed ? 'Scan all finished with errors' : 'Scan all completed',
    total: activeCompetitors.length,
    completed,
    queued,
    failed,
    items,
  }
}
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
  params: { q?: string; product_id?: number },
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
