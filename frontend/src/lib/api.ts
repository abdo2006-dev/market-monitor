import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
})

// Competitors
export const getCompetitors = () => api.get('/competitors').then(r => r.data)
export const createCompetitor = (data: any) => api.post('/competitors', data).then(r => r.data)
export const seedDefaultCompetitors = () => api.post('/competitors/seed-defaults').then(r => r.data)
export const updateCompetitor = (id: number, data: any) => api.put(`/competitors/${id}`, data).then(r => r.data)
export const deleteCompetitor = (id: number) => api.delete(`/competitors/${id}`)
export const scanNow = (id: number) => api.post(`/competitors/${id}/scan-now`).then(r => r.data)
export const scanAllCompetitors = async (competitors: any[], concurrency = 4) => {
  const activeCompetitors = competitors.filter(competitor => competitor.active)
  const items: any[] = []
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
export const getSearchSuggestions = (params: any) => api.get('/search/suggestions', { params }).then(r => r.data)
export const compareProduct = (params: any) => api.get('/search/compare', { params }).then(r => r.data)

// Dashboard
export const getDashboardSummary = () => api.get('/dashboard/summary').then(r => r.data)
export const getSalesTrends = (params: any) => api.get('/dashboard/sales-trends', { params }).then(r => r.data)

// Exports
export const collectionPricesExportUrl = (params: {
  competitor_id: string | number
  collection_url: string
  format: string
  max_pages: string | number
}) => {
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
