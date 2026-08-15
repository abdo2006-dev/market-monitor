import React, { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/layout/Sidebar'

const DashboardPage = lazy(() => import('./pages/Dashboard'))
const CompetitorsPage = lazy(() => import('./pages/Competitors'))
const ProductsPage = lazy(() => import('./pages/Products'))
const ProductDetailPage = lazy(() => import('./pages/ProductDetail'))
const MarketSearchPage = lazy(() => import('./pages/MarketSearch'))
const SalesTrendsPage = lazy(() => import('./pages/SalesTrends'))
const ExportsPage = lazy(() => import('./pages/Exports'))
const ActivityPage = lazy(() => import('./pages/Activity'))
const SettingsPage = lazy(() => import('./pages/Settings'))

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<div className="route-loading" aria-label="Loading page"><span /><span /><span /></div>}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/competitors" element={<CompetitorsPage />} />
          <Route path="/products" element={<ProductsPage />} />
          <Route path="/products/:id" element={<ProductDetailPage />} />
          <Route path="/search" element={<MarketSearchPage />} />
          <Route path="/sales" element={<SalesTrendsPage />} />
          <Route path="/exports" element={<ExportsPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}
