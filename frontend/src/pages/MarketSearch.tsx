import React, { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { searchProducts } from '../lib/api'
import { Card, Input, Button, Table, Tr, Td, StockBadge, Loading, EmptyState } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatPrice, timeAgo } from '../lib/utils'

export default function MarketSearchPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [inputVal, setInputVal] = useState(searchParams.get('q') || '')
  const [query, setQuery] = useState(searchParams.get('q') || '')
  const [page, setPage] = useState(1)

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['search', query, page],
    queryFn: () => searchProducts({ q: query, page }),
    enabled: !!query,
  })

  const handleSearch = () => {
    setQuery(inputVal.trim())
    setPage(1)
    if (inputVal.trim()) setSearchParams({ q: inputVal.trim() })
  }

  const products = data?.items || []
  const total = data?.total || 0
  const pageSize = data?.page_size || 50

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader title="Market Search" subtitle="Compare prices across all competitors" />

      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <Input
              value={inputVal}
              onChange={e => setInputVal(e.target.value)}
              placeholder="Search for a product, e.g. 'black hoodie', 'running shoes'..."
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              style={{ fontSize: 16, padding: '10px 14px' }}
            />
          </div>
          <Button onClick={handleSearch} loading={isLoading} style={{ padding: '10px 24px' }}>
            🔍 Search
          </Button>
        </div>
        {query && <div style={{ marginTop: 8, fontSize: 13, color: '#8b8fa8' }}>
          {isFetching ? 'Searching...' : `${total} results for "${query}" · sorted by price`}
        </div>}
      </Card>

      {!query ? (
        <Card>
          <EmptyState icon="🔍" title="Search across all competitors"
            description="Type a product name above to compare prices from all your monitored competitors." />
        </Card>
      ) : isLoading ? <Loading /> : products.length === 0 ? (
        <Card>
          <EmptyState icon="😶" title="No products found"
            description={`No products matched "${query}". Try a different search term.`} />
        </Card>
      ) : (
        <Card>
          <Table headers={['', 'Product', 'Competitor', 'Price', 'Stock', 'Last Checked', '']}>
            {products.map((p: any, i: number) => (
              <Tr key={p.id} onClick={() => navigate(`/products/${p.id}`)}>
                <Td style={{ width: 36, color: '#8b8fa8', fontWeight: 700 }}>
                  {i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `#${(page - 1) * pageSize + i + 1}`}
                </Td>
                <Td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    {p.image_url && (
                      <img src={p.image_url} alt="" style={{ width: 36, height: 36, objectFit: 'cover', borderRadius: 4 }}
                        onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                    )}
                    <div style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: '#e4e4f0' }}>
                      {p.title}
                    </div>
                  </div>
                </Td>
                <Td>
                  <span style={{ background: '#2d3048', padding: '3px 8px', borderRadius: 6, fontSize: 12, color: '#8b8fa8' }}>
                    {p.competitor_name}
                  </span>
                </Td>
                <Td>
                  <span style={{ fontWeight: 800, fontSize: 16, color: i === 0 ? '#22c55e' : '#e4e4f0' }}>
                    {formatPrice(p.current_price, p.currency)}
                  </span>
                </Td>
                <Td><StockBadge status={p.stock_status} /></Td>
                <Td style={{ color: '#8b8fa8', fontSize: 13 }}>{timeAgo(p.last_checked_at)}</Td>
                <Td>
                  <a href={p.url} target="_blank" rel="noopener noreferrer"
                    onClick={e => e.stopPropagation()}
                    style={{ color: '#6366f1', fontSize: 12, textDecoration: 'none' }}>↗</a>
                </Td>
              </Tr>
            ))}
          </Table>

          {total > pageSize && (
            <div style={{ display: 'flex', justifyContent: 'center', gap: 10, paddingTop: 16 }}>
              <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>← Prev</Button>
              <span style={{ padding: '5px 12px', color: '#8b8fa8', fontSize: 13 }}>Page {page}</span>
              <Button size="sm" variant="secondary" disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage(p => p + 1)}>Next →</Button>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
