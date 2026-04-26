import React from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { getProduct, getProductHistory } from '../lib/api'
import { Card, StockBadge, EventBadge, Table, Tr, Td, Loading, ErrorState } from '../components/ui'
import { formatPrice, formatDate, timeAgo } from '../lib/utils'
import { getEvents } from '../lib/api'

export default function ProductDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const productId = Number(id)

  const { data: product, isLoading: pLoading } = useQuery({
    queryKey: ['product', productId], queryFn: () => getProduct(productId),
  })
  const { data: history = [], isLoading: hLoading } = useQuery({
    queryKey: ['product-history', productId], queryFn: () => getProductHistory(productId),
  })
  const { data: eventsData } = useQuery({
    queryKey: ['product-events', productId],
    queryFn: () => getEvents({ product_id: productId, page_size: 30 }),
  })

  if (pLoading) return <div style={{ padding: '2rem' }}><Loading /></div>
  if (!product) return <div style={{ padding: '2rem' }}><ErrorState message="Product not found." /></div>

  const chartData = history.map((s: any) => ({
    date: new Date(s.checked_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    price: Number(s.price) || null,
  })).filter((d: any) => d.price !== null)

  const events = eventsData?.items || []

  return (
    <div style={{ padding: '2rem' }}>
      <button onClick={() => navigate(-1)} style={{ background: 'none', border: 'none', color: '#6366f1', cursor: 'pointer', marginBottom: 16, fontSize: 14 }}>
        ← Back
      </button>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 20, marginBottom: 20 }}>
        {/* Product Info */}
        <Card>
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
            {product.image_url && (
              <img src={product.image_url} alt="" style={{ width: 100, height: 100, objectFit: 'cover', borderRadius: 8 }} />
            )}
            <div style={{ flex: 1 }}>
              <h2 style={{ fontSize: 20, fontWeight: 700, color: '#e4e4f0', marginBottom: 8 }}>{product.title}</h2>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
                <StockBadge status={product.stock_status} />
                <span style={{ fontSize: 12, color: '#8b8fa8', background: '#2d3048', padding: '2px 8px', borderRadius: 6 }}>
                  {product.competitor_name}
                </span>
              </div>
              <div style={{ fontSize: 28, fontWeight: 800, color: '#6366f1' }}>
                {formatPrice(product.current_price, product.currency)}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, fontSize: 13, color: '#8b8fa8' }}>
            <div><div>First Seen</div><div style={{ color: '#c4c6d8' }}>{formatDate(product.first_seen_at)}</div></div>
            <div><div>Last Checked</div><div style={{ color: '#c4c6d8' }}>{timeAgo(product.last_checked_at)}</div></div>
            <div><div>Status</div><div style={{ color: product.active ? '#22c55e' : '#6b7280' }}>{product.active ? 'Active' : 'Inactive'}</div></div>
          </div>
          <div style={{ marginTop: 12 }}>
            <a href={product.url} target="_blank" rel="noopener noreferrer"
              style={{ color: '#6366f1', fontSize: 13 }}>
              ↗ View on website
            </a>
          </div>
        </Card>

        {/* Quick Stats */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Card>
            <div style={{ fontSize: 13, color: '#8b8fa8', marginBottom: 4 }}>Current Price</div>
            <div style={{ fontSize: 24, fontWeight: 800, color: '#e4e4f0' }}>{formatPrice(product.current_price, product.currency)}</div>
          </Card>
          {history.length >= 2 && (() => {
            const prices = history.map((s: any) => Number(s.price)).filter(Boolean)
            const min = Math.min(...prices)
            const max = Math.max(...prices)
            return (
              <>
                <Card>
                  <div style={{ fontSize: 13, color: '#8b8fa8', marginBottom: 4 }}>All-time Low</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: '#22c55e' }}>{formatPrice(min, product.currency)}</div>
                </Card>
                <Card>
                  <div style={{ fontSize: 13, color: '#8b8fa8', marginBottom: 4 }}>All-time High</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: '#ef4444' }}>{formatPrice(max, product.currency)}</div>
                </Card>
              </>
            )
          })()}
        </div>
      </div>

      {/* Price History Chart */}
      {chartData.length >= 2 && (
        <Card style={{ marginBottom: 20 }}>
          <h3 style={{ fontWeight: 700, marginBottom: 16 }}>Price History</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2d3048" />
              <XAxis dataKey="date" tick={{ fill: '#8b8fa8', fontSize: 12 }} />
              <YAxis tick={{ fill: '#8b8fa8', fontSize: 12 }} />
              <Tooltip contentStyle={{ background: '#1a1d2e', border: '1px solid #2d3048', borderRadius: 8 }}
                labelStyle={{ color: '#8b8fa8' }} itemStyle={{ color: '#6366f1' }} />
              <Line type="monotone" dataKey="price" stroke="#6366f1" strokeWidth={2} dot={{ fill: '#6366f1', r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Event History */}
      <Card style={{ marginBottom: 20 }}>
        <h3 style={{ fontWeight: 700, marginBottom: 16 }}>Event History</h3>
        {events.length === 0 ? (
          <div style={{ color: '#8b8fa8', padding: '1rem' }}>No events recorded</div>
        ) : (
          <Table headers={['Type', 'Message', 'Old', 'New', 'When']}>
            {events.map((e: any) => (
              <Tr key={e.id}>
                <Td><EventBadge type={e.event_type} /></Td>
                <Td style={{ color: '#c4c6d8' }}>{e.event_message || '—'}</Td>
                <Td style={{ color: '#8b8fa8', fontSize: 12 }}>{JSON.stringify(e.old_value) || '—'}</Td>
                <Td style={{ color: '#8b8fa8', fontSize: 12 }}>{JSON.stringify(e.new_value) || '—'}</Td>
                <Td style={{ color: '#8b8fa8', fontSize: 13 }}>{formatDate(e.detected_at)}</Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>

      {/* Snapshots */}
      <Card>
        <h3 style={{ fontWeight: 700, marginBottom: 16 }}>Snapshot History ({history.length})</h3>
        {hLoading ? <Loading /> : (
          <Table headers={['Date', 'Price', 'Stock', 'Title']}>
            {[...history].reverse().slice(0, 30).map((s: any) => (
              <Tr key={s.id}>
                <Td style={{ color: '#8b8fa8', fontSize: 13 }}>{formatDate(s.checked_at)}</Td>
                <Td style={{ fontWeight: 600 }}>{formatPrice(s.price, s.currency)}</Td>
                <Td><StockBadge status={s.stock_status} /></Td>
                <Td style={{ color: '#8b8fa8', fontSize: 13, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
