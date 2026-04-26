import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getProducts, getCompetitors } from '../lib/api'
import { Card, Button, Table, Tr, Td, StockBadge, Input, Select, Loading, EmptyState } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatPrice, timeAgo, formatDateShort } from '../lib/utils'

export default function ProductsPage() {
  const navigate = useNavigate()
  const [filters, setFilters] = useState({
    search: '', competitor_id: '', active: 'true', stock_status: '',
    min_price: '', max_price: '', sort: 'last_checked_at',
    page: 1,
  })

  const { data: competitors = [] } = useQuery({ queryKey: ['competitors'], queryFn: getCompetitors })
  const { data, isLoading } = useQuery({
    queryKey: ['products', filters],
    queryFn: () => getProducts({
      ...filters,
      competitor_id: filters.competitor_id || undefined,
      active: filters.active === '' ? undefined : filters.active === 'true',
      stock_status: filters.stock_status || undefined,
      min_price: filters.min_price || undefined,
      max_price: filters.max_price || undefined,
    }),
    placeholderData: prev => prev,
  })

  const set = (k: string) => (e: React.ChangeEvent<any>) =>
    setFilters(f => ({ ...f, [k]: e.target.value, page: 1 }))

  const products = data?.items || []
  const total = data?.total || 0
  const pageSize = data?.page_size || 50

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader title="Products" subtitle={`${total} products tracked`} />

      {/* Filters */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
          <Input placeholder="Search products..." value={filters.search} onChange={set('search')} />
          <Select value={filters.competitor_id} onChange={set('competitor_id')}>
            <option value="">All Competitors</option>
            {competitors.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Select value={filters.active} onChange={set('active')}>
            <option value="true">Active</option>
            <option value="false">Inactive</option>
            <option value="">All</option>
          </Select>
          <Select value={filters.stock_status} onChange={set('stock_status')}>
            <option value="">All Stock</option>
            <option value="in_stock">In Stock</option>
            <option value="out_of_stock">Out of Stock</option>
            <option value="unknown">Unknown</option>
          </Select>
          <Input placeholder="Min price" type="number" value={filters.min_price} onChange={set('min_price')} />
          <Input placeholder="Max price" type="number" value={filters.max_price} onChange={set('max_price')} />
          <Select value={filters.sort} onChange={set('sort')}>
            <option value="last_checked_at">Last Checked</option>
            <option value="price_asc">Price ↑</option>
            <option value="price_desc">Price ↓</option>
            <option value="first_seen">First Seen</option>
            <option value="title">Title</option>
          </Select>
        </div>
      </Card>

      <Card>
        {isLoading ? <Loading /> : products.length === 0 ? (
          <EmptyState icon="📦" title="No products found" description="Try adjusting your filters." />
        ) : (
          <>
            <Table headers={['', 'Product', 'Competitor', 'Price', 'Stock', 'First Seen', 'Last Checked', '']}>
              {products.map((p: any) => (
                <Tr key={p.id} onClick={() => navigate(`/products/${p.id}`)}>
                  <Td style={{ width: 48 }}>
                    {p.image_url ? (
                      <img src={p.image_url} alt="" style={{ width: 40, height: 40, objectFit: 'cover', borderRadius: 6 }}
                        onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                    ) : (
                      <div style={{ width: 40, height: 40, background: '#2d3048', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>📦</div>
                    )}
                  </Td>
                  <Td>
                    <div style={{ fontWeight: 500, color: '#e4e4f0', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.title}</div>
                  </Td>
                  <Td style={{ color: '#8b8fa8' }}>{p.competitor_name}</Td>
                  <Td>
                    <span style={{ fontWeight: 700, color: '#e4e4f0' }}>
                      {formatPrice(p.current_price, p.currency)}
                    </span>
                  </Td>
                  <Td><StockBadge status={p.stock_status} /></Td>
                  <Td style={{ color: '#8b8fa8', fontSize: 13 }}>{formatDateShort(p.first_seen_at)}</Td>
                  <Td style={{ color: '#8b8fa8', fontSize: 13 }}>{timeAgo(p.last_checked_at)}</Td>
                  <Td>
                    <a href={p.url} target="_blank" rel="noopener noreferrer"
                      onClick={e => e.stopPropagation()}
                      style={{ color: '#6366f1', fontSize: 12, textDecoration: 'none' }}>↗ View</a>
                  </Td>
                </Tr>
              ))}
            </Table>

            {/* Pagination */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 14, marginTop: 8, borderTop: '1px solid #2d3048' }}>
              <span style={{ color: '#8b8fa8', fontSize: 13 }}>{total} total products</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button size="sm" variant="secondary" disabled={filters.page <= 1}
                  onClick={() => setFilters(f => ({ ...f, page: f.page - 1 }))}>← Prev</Button>
                <span style={{ padding: '5px 12px', color: '#8b8fa8', fontSize: 13 }}>Page {filters.page} of {Math.ceil(total / pageSize)}</span>
                <Button size="sm" variant="secondary" disabled={filters.page >= Math.ceil(total / pageSize)}
                  onClick={() => setFilters(f => ({ ...f, page: f.page + 1 }))}>Next →</Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
