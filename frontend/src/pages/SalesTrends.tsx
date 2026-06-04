import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/layout/Sidebar'
import { Card, EmptyState, ErrorState, Loading, Select, StatCard, Table, Td, Tr } from '../components/ui'
import { getCompetitors, getSalesTrends } from '../lib/api'
import { formatDate, formatPrice } from '../lib/utils'

type Period = 'day' | 'week' | 'month'

type Competitor = {
  id: number
  name: string
}

type SalesCompetitor = {
  competitor_id: number
  competitor_name: string
  inferred_sold_count: number
  unique_products_count: number
  last_signal_at: string | null
}

type SalesProduct = {
  product_id: number
  title: string
  category?: string | null
  url: string
  image_url?: string | null
  current_price?: number | null
  currency: string
  stock_status: string
  competitor_id: number
  competitor_name: string
  inferred_sold_count: number
  last_signal_at: string | null
}

type SalesTrendsResponse = {
  period: Period
  since: string
  until: string
  total_inferred_sold: number
  competitors: SalesCompetitor[]
  top_products: SalesProduct[]
  note: string
}

const PERIODS: { value: Period; label: string }[] = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
]

export default function SalesTrendsPage() {
  const [period, setPeriod] = useState<Period>('day')
  const [competitorId, setCompetitorId] = useState('')
  const [competitors, setCompetitors] = useState<Competitor[]>([])
  const [data, setData] = useState<SalesTrendsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getCompetitors().then(setCompetitors).catch(() => setCompetitors([]))
  }, [])

  useEffect(() => {
    setLoading(true)
    setError(null)
    getSalesTrends({
      period,
      competitor_id: competitorId || undefined,
      limit: 50,
    })
      .then(setData)
      .catch(() => setError('Failed to load sales signals.'))
      .finally(() => setLoading(false))
  }, [period, competitorId])

  const topSite = data?.competitors.find(c => c.inferred_sold_count > 0)
  const topProduct = data?.top_products[0]
  const windowLabel = useMemo(() => {
    if (!data) return ''
    return `${formatDate(data.since)} to ${formatDate(data.until)}`
  }, [data])

  return (
    <div style={{ padding: '1.5rem' }}>
      <PageHeader
        title="Sales Signals"
        subtitle="Rank competitors and products by likely sales movement from scan events."
      />

      <Card style={{ marginBottom: '1rem', padding: '1rem' }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 13, color: '#8b8fa8', marginBottom: 6 }}>Period</div>
            <div style={{ display: 'inline-flex', border: '1px solid #2d3048', borderRadius: 8, overflow: 'hidden' }}>
              {PERIODS.map(item => (
                <button
                  key={item.value}
                  onClick={() => setPeriod(item.value)}
                  style={{
                    minWidth: 74,
                    padding: '8px 12px',
                    border: 'none',
                    borderRight: item.value === 'month' ? 'none' : '1px solid #2d3048',
                    background: period === item.value ? '#6366f1' : '#0f1117',
                    color: period === item.value ? '#fff' : '#c4c6d8',
                    cursor: 'pointer',
                    fontWeight: 600,
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div style={{ minWidth: 260 }}>
            <Select label="Competitor" value={competitorId} onChange={e => setCompetitorId(e.target.value)}>
              <option value="">All competitors</option>
              {competitors.map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </div>

          {data && (
            <div style={{ color: '#8b8fa8', fontSize: 13, paddingBottom: 8 }}>
              {windowLabel}
            </div>
          )}
        </div>
      </Card>

      {loading && <Loading text="Loading sales signals..." />}
      {error && <ErrorState message={error} />}

      {!loading && !error && data && (
        <>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
            gap: '1rem',
            marginBottom: '1rem',
          }}>
            <StatCard label="Inferred Sales" value={data.total_inferred_sold} icon="📈" color="#22c55e" />
            <StatCard label="Sites With Signals" value={data.competitors.filter(c => c.inferred_sold_count > 0).length} icon="🏢" color="#38bdf8" />
            <StatCard label="Top Site" value={topSite?.competitor_name || 'N/A'} icon="🏆" color="#f59e0b" />
            <StatCard label="Top Product" value={topProduct?.title || 'N/A'} icon="📦" color="#a855f7" />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 0.85fr) minmax(420px, 1.4fr)', gap: '1rem' }}>
            <Card>
              <h2 style={{ fontSize: 16, fontWeight: 700, color: '#e4e4f0', marginBottom: 12 }}>By Competitor</h2>
              <Table headers={['Site', 'Signals', 'Products', 'Last Signal']}>
                {data.competitors.map(c => (
                  <Tr key={c.competitor_id}>
                    <Td>
                      <button
                        onClick={() => setCompetitorId(String(c.competitor_id))}
                        style={{
                          background: 'none',
                          border: 'none',
                          padding: 0,
                          color: '#e4e4f0',
                          fontWeight: 700,
                          cursor: 'pointer',
                          textAlign: 'left',
                        }}
                      >
                        {c.competitor_name}
                      </button>
                    </Td>
                    <Td style={{ color: c.inferred_sold_count ? '#22c55e' : '#8b8fa8', fontWeight: 700 }}>
                      {c.inferred_sold_count}
                    </Td>
                    <Td>{c.unique_products_count}</Td>
                    <Td>{c.last_signal_at ? formatDate(c.last_signal_at) : 'N/A'}</Td>
                  </Tr>
                ))}
              </Table>
            </Card>

            <Card>
              <h2 style={{ fontSize: 16, fontWeight: 700, color: '#e4e4f0', marginBottom: 12 }}>Top Products</h2>
              {data.top_products.length === 0 ? (
                <EmptyState
                  icon="📭"
                  title="No sales signals yet"
                  description="Run more scans over time to catch products going out of stock or disappearing."
                />
              ) : (
                <Table headers={['Product', 'Site', 'Category', 'Signals', 'Price', 'Last Signal']}>
                  {data.top_products.map(p => (
                    <Tr key={`${p.competitor_id}-${p.product_id}`}>
                      <Td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 220 }}>
                          {p.image_url && (
                            <img
                              src={p.image_url}
                              alt=""
                              style={{
                                width: 34,
                                height: 34,
                                objectFit: 'cover',
                                borderRadius: 6,
                                border: '1px solid #2d3048',
                              }}
                            />
                          )}
                          <div>
                            <Link to={`/products/${p.product_id}`} style={{ color: '#e4e4f0', fontWeight: 700, textDecoration: 'none' }}>
                              {p.title}
                            </Link>
                            <div style={{ fontSize: 12, color: '#8b8fa8' }}>{p.stock_status.replaceAll('_', ' ')}</div>
                          </div>
                        </div>
                      </Td>
                      <Td>{p.competitor_name}</Td>
                      <Td>{p.category || 'Uncategorized'}</Td>
                      <Td style={{ color: '#22c55e', fontWeight: 700 }}>{p.inferred_sold_count}</Td>
                      <Td>{formatPrice(p.current_price, p.currency)}</Td>
                      <Td>{p.last_signal_at ? formatDate(p.last_signal_at) : 'N/A'}</Td>
                    </Tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>

          <div style={{ marginTop: '1rem', color: '#8b8fa8', fontSize: 13, lineHeight: 1.5 }}>
            {data.note}
          </div>
        </>
      )}
    </div>
  )
}
