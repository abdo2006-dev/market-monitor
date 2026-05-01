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

// Settings
export const getSettings = () => api.get('/settings').then(r => r.data)
export const updateSettings = (data: any) => api.put('/settings', data).then(r => r.data)

export default api
