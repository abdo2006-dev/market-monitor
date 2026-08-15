import React, { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  Activity,
  BarChart3,
  Boxes,
  Building2,
  Download,
  LayoutDashboard,
  Menu,
  Radar,
  Search,
  Settings,
  X,
} from 'lucide-react'

const MARKET_NAV = [
  { to: '/search', label: 'Search', icon: Search },
  { to: '/competitors', label: 'Competitors', icon: Building2 },
  { to: '/exports', label: 'Exports', icon: Download },
]

const SYSTEM_NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard },
  { to: '/products', label: 'Products', icon: Boxes },
  { to: '/sales', label: 'Sales signals', icon: BarChart3 },
  { to: '/activity', label: 'Activity', icon: Activity },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function AppNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="app-nav" aria-label="Primary navigation">
      <div className="nav-group">
        <span className="nav-group-label">Market</span>
        {MARKET_NAV.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} onClick={onNavigate} className={({ isActive }) => `nav-link ${isActive ? 'is-active' : ''}`}>
            <Icon size={18} aria-hidden="true" /><span>{label}</span>
          </NavLink>
        ))}
      </div>
      <div className="nav-group nav-group--system">
        <span className="nav-group-label">Workspace</span>
        {SYSTEM_NAV.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === '/'} onClick={onNavigate} className={({ isActive }) => `nav-link ${isActive ? 'is-active' : ''}`}>
            <Icon size={18} aria-hidden="true" /><span>{label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

function Brand() {
  return (
    <NavLink to="/search" className="app-brand" aria-label="Market Monitor Search">
      <span className="brand-mark"><Radar size={21} aria-hidden="true" /></span>
      <span><strong>Market Monitor</strong><small>Market intelligence</small></span>
    </NavLink>
  )
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const preview = import.meta.env.VITE_PREVIEW_DEMO_MODE === 'true'

  useEffect(() => setMobileOpen(false), [location.pathname])
  useEffect(() => {
    if (!mobileOpen) return
    const previousOverflow = document.body.style.overflow
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false)
    }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [mobileOpen])

  return (
    <div className="app-shell">
      <aside className="desktop-sidebar">
        <Brand />
        <AppNav />
        <div className="sidebar-foot">
          <span className={`environment-dot ${preview ? 'is-preview' : ''}`} />
          <span><strong>{preview ? 'Protected preview' : 'Market workspace'}</strong><small>{preview ? 'Isolated test data' : 'Daily operations'}</small></span>
        </div>
      </aside>

      <header className="mobile-header">
        <Brand />
        <button type="button" className="icon-button" onClick={() => setMobileOpen(open => !open)} aria-expanded={mobileOpen} aria-controls="mobile-navigation" aria-label={mobileOpen ? 'Close navigation' : 'Open navigation'}>
          {mobileOpen ? <X size={21} /> : <Menu size={21} />}
        </button>
      </header>

      {mobileOpen && (
        <div className="mobile-drawer-backdrop" onClick={() => setMobileOpen(false)}>
          <aside id="mobile-navigation" className="mobile-drawer" role="dialog" aria-modal="true" aria-label="Application navigation" onClick={event => event.stopPropagation()}>
            <div className="mobile-drawer-head"><span>Navigate</span><button type="button" className="icon-button" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={20} /></button></div>
            <AppNav onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <main className="app-main">{children}</main>

      <nav className="mobile-tabbar" aria-label="Daily workflows">
        {MARKET_NAV.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} className={({ isActive }) => `mobile-tab ${isActive ? 'is-active' : ''}`}>
            <Icon size={19} aria-hidden="true" /><span>{label}</span>
          </NavLink>
        ))}
        <button type="button" className={`mobile-tab ${mobileOpen ? 'is-active' : ''}`} onClick={() => setMobileOpen(true)} aria-label="Open more navigation">
          <Menu size={19} aria-hidden="true" /><span>More</span>
        </button>
      </nav>
    </div>
  )
}

export function PageHeader({ title, subtitle, eyebrow, action }: {
  title: string; subtitle?: string; eyebrow?: string; action?: React.ReactNode
}) {
  return (
    <header className="page-header">
      <div className="page-heading">
        {eyebrow && <span className="page-eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action && <div className="page-actions">{action}</div>}
    </header>
  )
}
