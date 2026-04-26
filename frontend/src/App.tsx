import React from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/layout/Sidebar'
import DashboardPage from './pages/Dashboard'
import CompetitorsPage from './pages/Competitors'
import ProductsPage from './pages/Products'
import ProductDetailPage from './pages/ProductDetail'
import MarketSearchPage from './pages/MarketSearch'
import ActivityPage from './pages/Activity'
import SettingsPage from './pages/Settings'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/competitors" element={<CompetitorsPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/products/:id" element={<ProductDetailPage />} />
        <Route path="/search" element={<MarketSearchPage />} />
        <Route path="/activity" element={<ActivityPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </Layout>
  )
}
