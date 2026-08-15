import React, { useEffect } from 'react'
import { AlertTriangle, Inbox, LoaderCircle, X } from 'lucide-react'
import { cn, EVENT_COLORS, EVENT_LABELS, STOCK_LABELS } from '../../lib/utils'

// ── Card ─────────────────────────────────────────────────────────────────────

export function Card({ children, className, style }: { children: React.ReactNode; className?: string; style?: React.CSSProperties }) {
  return <div className={cn('card', className)} style={style}>{children}</div>
}

export function StatCard({ label, value, icon, color = '#6366f1' }: { label: string; value: number | string; icon: React.ReactNode; color?: string }) {
  return (
    <Card className="ui-stat-card">
      <div className="ui-stat-copy">
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <div className="ui-stat-icon" style={{ color, borderColor: `${color}44`, background: `${color}12` }}>
        {icon}
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
  return (
    <button
      {...props}
      className={cn('ui-button', `ui-button--${variant}`, `ui-button--${size}`, className)}
      style={style}
      disabled={loading || props.disabled}
    >
      {loading && <span className="ui-spinner" aria-hidden="true" />}
      {children}
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
  return <span className={cn('stock-indicator', `stock-indicator--${status}`)}>{info.label}</span>
}

// ── Input ─────────────────────────────────────────────────────────────────────

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
}

export function Input({ label, id, style, ...props }: InputProps) {
  if (label) {
    return (
      <div className="field">
        <label htmlFor={id} className="field-label">{label}</label>
        <input id={id} {...props} className={cn('ui-input', props.className)} style={style} />
      </div>
    )
  }
  return <input {...props} className={cn('ui-input', props.className)} style={style} />
}

export function Select({ label, id, children, style, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  if (label) {
    return (
      <div className="field">
        <label htmlFor={id} className="field-label">{label}</label>
        <select id={id} {...props} className={cn('ui-select', props.className)} style={style}>{children}</select>
      </div>
    )
  }
  return <select {...props} className={cn('ui-select', props.className)} style={style}>{children}</select>
}

export function Textarea({ label, id, style, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }) {
  if (label) {
    return (
      <div className="field">
        <label htmlFor={id} className="field-label">{label}</label>
        <textarea id={id} {...props} className={cn('ui-textarea', props.className)} style={style} />
      </div>
    )
  }
  return <textarea {...props} className={cn('ui-textarea', props.className)} style={style} />
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export function Modal({ open, onClose, title, children, width = 600 }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode; width?: number
}) {
  useEffect(() => {
    if (!open) return
    const previousOverflow = document.body.style.overflow
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [onClose, open])

  if (!open) return null
  return (
    <div className="ui-modal-backdrop" onClick={onClose}>
      <div className="ui-modal" role="dialog" aria-modal="true" aria-label={title} style={{ maxWidth: width }} onClick={e => e.stopPropagation()}>
        <div className="ui-modal-head">
          <h3>{title}</h3>
          <button onClick={onClose} className="icon-button" aria-label={`Close ${title}`}><X size={19} /></button>
        </div>
        <div className="ui-modal-body">{children}</div>
      </div>
    </div>
  )
}

// ── Table ─────────────────────────────────────────────────────────────────────

export function Table({ headers, children }: { headers: string[]; children: React.ReactNode }) {
  return (
    <div className="ui-table-wrap">
      <table className="ui-table">
        <thead>
          <tr>
            {headers.map(h => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Tr({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) {
  return <tr onClick={onClick} style={{ cursor: onClick ? 'pointer' : undefined }}>{children}</tr>
}

export function Td({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <td style={style}>{children}</td>
}

// ── Loading / Empty ────────────────────────────────────────────────────────────

export function Loading({ text = 'Loading...' }: { text?: string }) {
  return (
    <div className="ui-state" role="status">
      <div><span className="ui-state-icon"><LoaderCircle className="ui-spinner" size={22} /></span><div>{text}</div></div>
    </div>
  )
}

export function EmptyState({ icon, title, description }: { icon?: React.ReactNode; title: string; description?: string }) {
  return (
    <div className="ui-state">
      <div><span className="ui-state-icon">{icon || <Inbox size={21} />}</span><h3>{title}</h3>{description && <p>{description}</p>}</div>
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="ui-state ui-state--error" role="alert">
      <div><span className="ui-state-icon"><AlertTriangle size={21} /></span><h3>Something needs attention</h3><p>{message}</p></div>
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
