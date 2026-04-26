import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getEvents, getCompetitors } from '../lib/api'
import { Card, EventBadge, Select, Loading, EmptyState, Button } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatPrice } from '../lib/utils'

const EVENT_TYPES = [
  'new_product', 'price_decrease', 'price_increase', 'price_changed',
  'stock_in', 'stock_out', 'product_removed', 'scrape_failed',
]

function groupByDate(events: any[]) {
  const groups: Record<string, any[]> = {}
  for (const e of events) {
    const date = new Date(e.detected_at).toLocaleDateString('en-US', {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
    })
    if (!groups[date]) groups[date] = []
    groups[date].push(e)
  }
  return groups
}

function EventDetail({ event }: { event: any }) {
  if (event.event_type === 'price_decrease' || event.event_type === 'price_increase') {
    const ov = event.old_value || {}
    const nv = event.new_value || {}
    const isDown = event.event_type === 'price_decrease'
    return (
      <span style={{ color: isDown ? '#22c55e' : '#ef4444', fontSize: 13 }}>
        {formatPrice(ov.price)} → {formatPrice(nv.price)}
        {nv.diff_percentage != null && ` (${nv.diff_percentage > 0 ? '+' : ''}${nv.diff_percentage.toFixed(1)}%)`}
      </span>
    )
  }
  if (event.event_type === 'new_product') {
    return <span style={{ color: '#22c55e', fontSize: 13 }}>{formatPrice(event.new_value?.price)}</span>
  }
  if (event.event_type === 'stock_in') {
    return <span style={{ color: '#3b82f6', fontSize: 13 }}>Back in stock</span>
  }
  if (event.event_type === 'stock_out') {
    return <span style={{ color: '#f97316', fontSize: 13 }}>Out of stock</span>
  }
  if (event.event_type === 'scrape_failed') {
    return <span style={{ color: '#ef4444', fontSize: 13 }}>{event.event_message?.slice(0, 60) || 'Scan error'}</span>
  }
  return null
}

export default function ActivityPage() {
  const navigate = useNavigate()
  const [filters, setFilters] = useState({ event_type: '', competitor_id: '', page: 1 })

  const { data: competitors = [] } = useQuery({ queryKey: ['competitors'], queryFn: getCompetitors })
  const { data, isLoading } = useQuery({
    queryKey: ['events', filters],
    queryFn: () => getEvents({
      event_type: filters.event_type || undefined,
      competitor_id: filters.competitor_id || undefined,
      page: filters.page,
      page_size: 100,
    }),
    placeholderData: prev => prev,
  })

  const events = data?.items || []
  const total = data?.total || 0
  const groups = groupByDate(events)

  const set = (k: string) => (e: React.ChangeEvent<any>) =>
    setFilters(f => ({ ...f, [k]: e.target.value, page: 1 }))

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader title="Activity" subtitle={`${total} total events`} />

      {/* Filters */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Select value={filters.event_type} onChange={set('event_type')} style={{ width: 200 }}>
            <option value="">All Event Types</option>
            {EVENT_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
          </Select>
          <Select value={filters.competitor_id} onChange={set('competitor_id')} style={{ width: 200 }}>
            <option value="">All Competitors</option>
            {competitors.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </div>
      </Card>

      {isLoading ? <Loading /> : events.length === 0 ? (
        <Card>
          <EmptyState icon="📋" title="No activity yet"
            description="Events will appear here once your competitors are scanned." />
        </Card>
      ) : (
        <>
          {Object.entries(groups).map(([date, dayEvents]) => (
            <div key={date} style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#8b8fa8', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
                <span>{date}</span>
                <span style={{ background: '#2d3048', borderRadius: 10, padding: '2px 8px', fontSize: 12 }}>{dayEvents.length}</span>
              </div>
              <Card>
                {dayEvents.map((event: any, idx: number) => (
                  <div key={event.id} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 0',
                    borderBottom: idx < dayEvents.length - 1 ? '1px solid #1e2235' : 'none',
                    cursor: event.product_id ? 'pointer' : 'default',
                  }} onClick={() => event.product_id && navigate(`/products/${event.product_id}`)}>
                    {/* Time */}
                    <div style={{ minWidth: 48, color: '#8b8fa8', fontSize: 12, paddingTop: 2 }}>
                      {new Date(event.detected_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                    </div>

                    {/* Badge */}
                    <div style={{ minWidth: 110 }}>
                      <EventBadge type={event.event_type} />
                    </div>

                    {/* Competitor */}
                    <div style={{ minWidth: 120, color: '#8b8fa8', fontSize: 13 }}>
                      {event.competitor_name}
                    </div>

                    {/* Product */}
                    <div style={{ flex: 1 }}>
                      <div style={{ color: '#e4e4f0', fontSize: 14, marginBottom: 2 }}>
                        {event.product_title || event.event_message || '—'}
                      </div>
                      <EventDetail event={event} />
                    </div>
                  </div>
                ))}
              </Card>
            </div>
          ))}

          {/* Pagination */}
          <div style={{ display: 'flex', justifyContent: 'center', gap: 10, paddingTop: 8 }}>
            <Button size="sm" variant="secondary" disabled={filters.page <= 1}
              onClick={() => setFilters(f => ({ ...f, page: f.page - 1 }))}>← Newer</Button>
            <span style={{ padding: '5px 12px', color: '#8b8fa8', fontSize: 13 }}>Page {filters.page}</span>
            <Button size="sm" variant="secondary" disabled={events.length < 100}
              onClick={() => setFilters(f => ({ ...f, page: f.page + 1 }))}>Older →</Button>
          </div>
        </>
      )}
    </div>
  )
}
