export function cn(...classes: (string | undefined | false | null)[]) {
  return classes.filter(Boolean).join(' ')
}

export function formatPrice(price: number | null | undefined, currency = 'USD') {
  if (price == null) return 'N/A'
  const symbols: Record<string, string> = { USD: '$', EUR: '€', GBP: '£', JPY: '¥' }
  const sym = symbols[currency] || currency + ' '
  return `${sym}${Number(price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function formatDate(dateStr: string | null | undefined) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

export function formatDateShort(dateStr: string | null | undefined) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export function timeAgo(dateStr: string | null | undefined) {
  if (!dateStr) return '—'
  const now = Date.now()
  const diff = now - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export const EVENT_COLORS: Record<string, string> = {
  new_product: '#22c55e',
  price_decrease: '#22c55e',
  price_increase: '#ef4444',
  price_changed: '#f59e0b',
  stock_in: '#3b82f6',
  stock_out: '#f97316',
  product_removed: '#6b7280',
  scrape_failed: '#ef4444',
}

export const EVENT_LABELS: Record<string, string> = {
  new_product: 'New Product',
  price_decrease: 'Price Drop',
  price_increase: 'Price Rise',
  price_changed: 'Price Changed',
  stock_in: 'Back In Stock',
  stock_out: 'Out of Stock',
  product_removed: 'Removed',
  scrape_failed: 'Scan Failed',
}

export const STOCK_LABELS: Record<string, { label: string; color: string }> = {
  in_stock: { label: 'In Stock', color: '#22c55e' },
  out_of_stock: { label: 'Out of Stock', color: '#ef4444' },
  unknown: { label: 'Unknown', color: '#6b7280' },
}
