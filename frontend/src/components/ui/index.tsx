import React from 'react'
import { cn, EVENT_COLORS, EVENT_LABELS, STOCK_LABELS } from '../../lib/utils'

// ── Card ─────────────────────────────────────────────────────────────────────

export function Card({ children, className, style }: { children: React.ReactNode; className?: string; style?: React.CSSProperties }) {
  return (
    <div className={cn('card', className)} style={{
      background: '#1a1d2e', border: '1px solid #2d3048', borderRadius: 12,
      padding: '1.25rem', boxShadow: '0 4px 24px rgba(0,0,0,0.2)',
      ...style,
    }}>
      {children}
    </div>
  )
}

export function StatCard({ label, value, icon, color = '#6366f1' }: { label: string; value: number | string; icon: React.ReactNode; color?: string }) {
  return (
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 13, color: '#8b8fa8', marginBottom: 6 }}>{label}</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: '#e4e4f0' }}>{value}</div>
        </div>
        <div style={{ background: color + '22', borderRadius: 10, padding: 10, color }}>
          {icon}
        </div>
      </div>
    </Card>
  )
}

// ── Button ────────────────────────────────────────────────────────────────────

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'sm' | 'md'
  loading?: boolean
}

export function Button({ children, variant = 'primary', size = 'md', loading, className, style, ...props }: ButtonProps) {
  const styles: Record<string, React.CSSProperties> = {
    primary: { background: '#6366f1', color: '#fff', border: 'none' },
    secondary: { background: '#2d3048', color: '#e4e4f0', border: '1px solid #3d3f5a' },
    danger: { background: '#ef4444', color: '#fff', border: 'none' },
    ghost: { background: 'transparent', color: '#8b8fa8', border: '1px solid #2d3048' },
  }
  const sizeStyles = {
    sm: { padding: '5px 12px', fontSize: 13 },
    md: { padding: '8px 18px', fontSize: 14 },
  }
  return (
    <button
      {...props}
      style={{
        borderRadius: 8, cursor: loading || props.disabled ? 'not-allowed' : 'pointer',
        fontWeight: 500, opacity: loading || props.disabled ? 0.6 : 1,
        transition: 'all 0.15s', ...styles[variant], ...sizeStyles[size], ...style,
      }}
    >
      {loading ? '⏳ Loading...' : children}
    </button>
  )
}

// ── Badge ─────────────────────────────────────────────────────────────────────

export function EventBadge({ type }: { type: string }) {
  const color = EVENT_COLORS[type] || '#6b7280'
  const label = EVENT_LABELS[type] || type
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      background: color + '22', color, border: `1px solid ${color}44`,
      borderRadius: 6, padding: '2px 8px', fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  )
}

export function StockBadge({ status }: { status: string }) {
  const info = STOCK_LABELS[status] || { label: status, color: '#6b7280' }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center',
      background: info.color + '22', color: info.color, border: `1px solid ${info.color}44`,
      borderRadius: 6, padding: '2px 8px', fontSize: 12, fontWeight: 600,
    }}>
      {info.label}
    </span>
  )
}

// ── Input ─────────────────────────────────────────────────────────────────────

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
}

export function Input({ label, id, style, ...props }: InputProps) {
  const inputStyle: React.CSSProperties = {
    width: '100%', background: '#0f1117', border: '1px solid #2d3048',
    borderRadius: 8, padding: '8px 12px', color: '#e4e4f0', fontSize: 14,
    outline: 'none', ...style,
  }
  if (label) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <label htmlFor={id} style={{ fontSize: 13, color: '#8b8fa8' }}>{label}</label>
        <input id={id} {...props} style={inputStyle} />
      </div>
    )
  }
  return <input {...props} style={inputStyle} />
}

export function Select({ label, id, children, style, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  const selectStyle: React.CSSProperties = {
    width: '100%', background: '#0f1117', border: '1px solid #2d3048',
    borderRadius: 8, padding: '8px 12px', color: '#e4e4f0', fontSize: 14,
    outline: 'none', cursor: 'pointer', ...style,
  }
  if (label) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <label htmlFor={id} style={{ fontSize: 13, color: '#8b8fa8' }}>{label}</label>
        <select id={id} {...props} style={selectStyle}>{children}</select>
      </div>
    )
  }
  return <select {...props} style={selectStyle}>{children}</select>
}

export function Textarea({ label, id, style, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }) {
  const s: React.CSSProperties = {
    width: '100%', background: '#0f1117', border: '1px solid #2d3048',
    borderRadius: 8, padding: '8px 12px', color: '#e4e4f0', fontSize: 14,
    outline: 'none', resize: 'vertical', ...style,
  }
  if (label) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <label htmlFor={id} style={{ fontSize: 13, color: '#8b8fa8' }}>{label}</label>
        <textarea id={id} {...props} style={s} />
      </div>
    )
  }
  return <textarea {...props} style={s} />
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export function Modal({ open, onClose, title, children, width = 600 }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode; width?: number
}) {
  if (!open) return null
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div style={{
        background: '#1a1d2e', border: '1px solid #2d3048', borderRadius: 14,
        width: '100%', maxWidth: width, maxHeight: '90vh', overflowY: 'auto',
        boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1.25rem 1.5rem', borderBottom: '1px solid #2d3048' }}>
          <h3 style={{ fontWeight: 700, fontSize: 17 }}>{title}</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#8b8fa8', cursor: 'pointer', fontSize: 20 }}>×</button>
        </div>
        <div style={{ padding: '1.5rem' }}>{children}</div>
      </div>
    </div>
  )
}

// ── Table ─────────────────────────────────────────────────────────────────────

export function Table({ headers, children }: { headers: string[]; children: React.ReactNode }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
        <thead>
          <tr>
            {headers.map(h => (
              <th key={h} style={{
                padding: '10px 14px', textAlign: 'left', color: '#8b8fa8',
                fontWeight: 600, fontSize: 12, textTransform: 'uppercase',
                borderBottom: '1px solid #2d3048', letterSpacing: 0.5,
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Tr({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) {
  return (
    <tr onClick={onClick} style={{
      borderBottom: '1px solid #1e2235', cursor: onClick ? 'pointer' : undefined,
      transition: 'background 0.1s',
    }}
      onMouseEnter={e => { if (onClick) (e.currentTarget as HTMLElement).style.background = '#222640' }}
      onMouseLeave={e => { if (onClick) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >{children}</tr>
  )
}

export function Td({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <td style={{ padding: '11px 14px', color: '#c4c6d8', verticalAlign: 'middle', ...style }}>{children}</td>
}

// ── Loading / Empty ────────────────────────────────────────────────────────────

export function Loading({ text = 'Loading...' }: { text?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '3rem', color: '#8b8fa8' }}>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 32, marginBottom: 8 }}>⏳</div>
        <div>{text}</div>
      </div>
    </div>
  )
}

export function EmptyState({ icon = '📭', title, description }: { icon?: string; title: string; description?: string }) {
  return (
    <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#8b8fa8' }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>{icon}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color: '#c4c6d8', marginBottom: 6 }}>{title}</div>
      {description && <div style={{ fontSize: 14 }}>{description}</div>}
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div style={{ textAlign: 'center', padding: '3rem', color: '#ef4444' }}>
      <div style={{ fontSize: 32, marginBottom: 8 }}>⚠️</div>
      <div>{message}</div>
    </div>
  )
}

// ── Price Delta ───────────────────────────────────────────────────────────────

export function PriceDelta({ oldPrice, newPrice, currency = 'USD' }: { oldPrice?: number; newPrice?: number; currency?: string }) {
  if (oldPrice == null || newPrice == null) return null
  const diff = newPrice - oldPrice
  const pct = ((diff / oldPrice) * 100).toFixed(1)
  const isDown = diff < 0
  return (
    <span style={{ color: isDown ? '#22c55e' : '#ef4444', fontWeight: 600, fontSize: 13 }}>
      {isDown ? '▼' : '▲'} {Math.abs(diff).toFixed(2)} ({isDown ? '' : '+'}{pct}%)
    </span>
  )
}
