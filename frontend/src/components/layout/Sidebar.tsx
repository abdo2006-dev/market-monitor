import React from 'react'
import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Dashboard', icon: '🏠' },
  { to: '/competitors', label: 'Competitors', icon: '🏢' },
  { to: '/products', label: 'Products', icon: '📦' },
  { to: '/search', label: 'Market Search', icon: '🔍' },
  { to: '/activity', label: 'Activity', icon: '📋' },
  { to: '/settings', label: 'Settings', icon: '⚙️' },
]

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* Sidebar */}
      <aside style={{
        width: 220, background: '#13162a', borderRight: '1px solid #2d3048',
        display: 'flex', flexDirection: 'column', position: 'fixed', top: 0, left: 0, bottom: 0, zIndex: 100,
      }}>
        {/* Logo */}
        <div style={{ padding: '1.5rem 1.25rem', borderBottom: '1px solid #2d3048' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ fontSize: 22 }}>📡</div>
            <div>
              <div style={{ fontWeight: 800, fontSize: 15, color: '#e4e4f0', letterSpacing: -0.3 }}>Market</div>
              <div style={{ fontWeight: 800, fontSize: 15, color: '#6366f1', letterSpacing: -0.3 }}>Monitor</div>
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav style={{ flex: 1, padding: '0.75rem 0.5rem' }}>
          {NAV.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '9px 14px', borderRadius: 8, marginBottom: 2,
                textDecoration: 'none', fontSize: 14, fontWeight: 500,
                transition: 'all 0.15s',
                background: isActive ? '#6366f122' : 'transparent',
                color: isActive ? '#6366f1' : '#8b8fa8',
                borderLeft: isActive ? '3px solid #6366f1' : '3px solid transparent',
              })}
            >
              <span style={{ fontSize: 16 }}>{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div style={{ padding: '1rem 1.25rem', borderTop: '1px solid #2d3048', color: '#3d3f5a', fontSize: 11 }}>
          Market Monitor v1.0
        </div>
      </aside>

      {/* Main content */}
      <main style={{ marginLeft: 220, flex: 1, minHeight: '100vh', background: '#0f1117' }}>
        {children}
      </main>
    </div>
  )
}

export function PageHeader({ title, subtitle, action }: {
  title: string; subtitle?: string; action?: React.ReactNode
}) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
      marginBottom: '1.5rem', flexWrap: 'wrap', gap: 12,
    }}>
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e4e4f0', marginBottom: 3 }}>{title}</h1>
        {subtitle && <p style={{ color: '#8b8fa8', fontSize: 14 }}>{subtitle}</p>}
      </div>
      {action && <div>{action}</div>}
    </div>
  )
}
