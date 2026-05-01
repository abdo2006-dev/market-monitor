import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getDashboardSummary, getCompetitors } from '../lib/api'
import { StatCard, Card, Table, Tr, Td, EventBadge, Loading, ErrorState, Select } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatPrice, timeAgo } from '../lib/utils'

export default function DashboardPage() {
  const [competitorFilter, setCompetitorFilter] = useState('')
  const { data, isLoading, error } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })
  const { data: competitors = [] } = useQuery({ queryKey: ['competitors'], queryFn: getCompetitors })

  if (isLoading) return <div style={{ padding: '2rem' }}><Loading /></div>
  if (error) return <div style={{ padding: '2rem' }}><ErrorState message="Failed to load dashboard." /></div>

  const events = (data?.latest_events || []).filter((event: any) =>
    !competitorFilter || String(event.competitor_id) === competitorFilter
  )
  const attention = data?.competitors_needing_attention || []

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader title="Dashboard" subtitle="Real-time competitor monitoring overview" />

      {/* Stat Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16, marginBottom: '2rem' }}>
        <StatCard label="New Products Today" value={data?.new_products_today ?? 0} icon="🆕" color="#22c55e" />
        <StatCard label="Price Changes Today" value={data?.price_changes_today ?? 0} icon="💱" color="#f59e0b" />
        <StatCard label="Price Drops Today" value={data?.price_drops_today ?? 0} icon="📉" color="#22c55e" />
        <StatCard label="Failed Scans Today" value={data?.failed_scans_today ?? 0} icon="❌" color="#ef4444" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 350px', gap: 20 }}>
        {/* Latest Events */}
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 16 }}>
            <h3 style={{ fontWeight: 700 }}>Latest Events</h3>
            <Select value={competitorFilter} onChange={e => setCompetitorFilter(e.target.value)} style={{ width: 190 }}>
              <option value="">All Competitors</option>
              {competitors.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
          </div>
          {events.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '2rem', color: '#8b8fa8' }}>No events yet</div>
          ) : (
            <Table headers={['Event', 'Competitor', 'Category', 'Product', 'Details', 'When']}>
              {events.slice(0, 15).map((e: any) => (
                <Tr key={e.id}>
                  <Td><EventBadge type={e.event_type} /></Td>
                  <Td style={{ color: '#e4e4f0', fontWeight: 500 }}>{e.competitor_name}</Td>
                  <Td style={{ color: '#8b8fa8', fontSize: 12 }}>{e.product_category || '—'}</Td>
                  <Td style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {e.product_title || '—'}
                  </Td>
                  <Td>
                    {e.event_type === 'price_decrease' && e.new_value && (
                      <span style={{ color: '#22c55e', fontWeight: 600 }}>
                        {formatPrice(e.old_value?.price)} → {formatPrice(e.new_value?.price)}
                      </span>
                    )}
                    {e.event_type === 'price_increase' && e.new_value && (
                      <span style={{ color: '#ef4444', fontWeight: 600 }}>
                        {formatPrice(e.old_value?.price)} → {formatPrice(e.new_value?.price)}
                      </span>
                    )}
                    {e.event_type === 'new_product' && (
                      <span style={{ color: '#22c55e' }}>{formatPrice(e.new_value?.price)}</span>
                    )}
                    {['stock_in', 'stock_out'].includes(e.event_type) && (
                      <span>{e.old_value?.stock_status} → {e.new_value?.stock_status}</span>
                    )}
                  </Td>
                  <Td style={{ color: '#8b8fa8' }}>{timeAgo(e.detected_at)}</Td>
                </Tr>
              ))}
            </Table>
          )}
        </Card>

        {/* Competitors needing attention */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card>
            <h3 style={{ fontWeight: 700, marginBottom: 14 }}>⚠️ Needs Attention</h3>
            {attention.length === 0 ? (
              <div style={{ color: '#22c55e', fontSize: 14 }}>✅ All competitors healthy</div>
            ) : (
              attention.map((c: any) => (
                <div key={c.id} style={{
                  background: '#ef444411', border: '1px solid #ef444433', borderRadius: 8,
                  padding: '10px 12px', marginBottom: 8,
                }}>
                  <div style={{ fontWeight: 600, color: '#e4e4f0', marginBottom: 2 }}>{c.name}</div>
                  <div style={{ fontSize: 12, color: '#ef4444' }}>Last scan: failed</div>
                </div>
              ))
            )}
          </Card>

          <Card>
            <h3 style={{ fontWeight: 700, marginBottom: 14 }}>📊 Today's Summary</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { label: 'Price Increases', value: data?.price_increases_today ?? 0, color: '#ef4444' },
                { label: 'Price Drops', value: data?.price_drops_today ?? 0, color: '#22c55e' },
                { label: 'New Products', value: data?.new_products_today ?? 0, color: '#6366f1' },
              ].map(item => (
                <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 14, color: '#8b8fa8' }}>{item.label}</span>
                  <span style={{ fontWeight: 700, color: item.color }}>{item.value}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
