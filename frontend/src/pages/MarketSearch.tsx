import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { compareProduct, getSearchSuggestions } from '../lib/api'
import { Card, Input, Button, Table, Tr, Td, StockBadge, Loading, EmptyState } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatPrice, timeAgo } from '../lib/utils'

export default function MarketSearchPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [inputVal, setInputVal] = useState(searchParams.get('q') || '')
  const [query, setQuery] = useState(searchParams.get('q') || '')
  const [selected, setSelected] = useState<any>(null)

  const suggestionsQuery = useQuery({
    queryKey: ['search-suggestions', query],
    queryFn: () => getSearchSuggestions({ q: query }),
    enabled: !!query && !selected,
  })

  const comparisonQuery = useQuery({
    queryKey: ['compare-product', selected?.representative_product_id],
    queryFn: () => compareProduct({ product_id: selected.representative_product_id }),
    enabled: !!selected,
  })

  const handleSearch = () => {
    const next = inputVal.trim()
    setQuery(next)
    setSelected(null)
    if (next) setSearchParams({ q: next })
  }

  const suggestions = suggestionsQuery.data?.items || []
  const comparison = comparisonQuery.data
  const rows = comparison?.items || []
  const foundRows = rows.filter((row: any) => row.product)

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader title="Market Search" subtitle="Choose an item, then compare it across competitors" />

      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <Input
              value={inputVal}
              onChange={e => setInputVal(e.target.value)}
              placeholder="Search for an item, e.g. Chill Knife, Batwing, Chillin Chili..."
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              style={{ fontSize: 16, padding: '10px 14px' }}
            />
          </div>
          <Button onClick={handleSearch} loading={suggestionsQuery.isLoading || comparisonQuery.isLoading} style={{ padding: '10px 24px' }}>
            Search
          </Button>
        </div>
        {query && !selected && (
          <div style={{ marginTop: 8, fontSize: 13, color: '#8b8fa8' }}>
            {suggestionsQuery.isFetching ? 'Finding possible items...' : `${suggestions.length} possible items for "${query}"`}
          </div>
        )}
        {selected && (
          <div style={{ marginTop: 8, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: '#8b8fa8' }}>Comparing</span>
            <span style={{ fontSize: 13, color: '#e4e4f0', fontWeight: 700 }}>{selected.title}</span>
            <Button size="sm" variant="ghost" onClick={() => setSelected(null)}>Choose another</Button>
          </div>
        )}
      </Card>

      {!query ? (
        <Card>
          <EmptyState icon="🔍" title="Search across all competitors"
            description="Type an item name, choose the intended item, then compare prices site by site." />
        </Card>
      ) : !selected ? (
        suggestionsQuery.isLoading ? <Loading text="Finding possible items..." /> : suggestions.length === 0 ? (
          <Card>
            <EmptyState icon="😶" title="No possible items found"
              description={`No products matched "${query}". Try a different spelling or a shorter term.`} />
          </Card>
        ) : (
          <Card>
            <Table headers={['Item', 'Seen On', 'Best Price', 'Category', 'Match']}>
              {suggestions.map((item: any) => (
                <Tr key={item.normalized_title} onClick={() => setSelected(item)}>
                  <Td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      {item.image_url && (
                        <img src={item.image_url} alt="" style={{ width: 38, height: 38, objectFit: 'cover', borderRadius: 4 }}
                          onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                      )}
                      <div>
                        <div style={{ color: '#e4e4f0', fontWeight: 700 }}>{item.title}</div>
                        <div style={{ color: '#8b8fa8', fontSize: 12 }}>{item.variants.join(', ')}</div>
                      </div>
                    </div>
                  </Td>
                  <Td style={{ color: '#8b8fa8', fontSize: 13 }}>
                    {item.competitors_count} competitor{item.competitors_count === 1 ? '' : 's'}
                  </Td>
                  <Td style={{ color: '#e4e4f0', fontWeight: 800 }}>{formatPrice(item.best_price, item.currency)}</Td>
                  <Td style={{ color: '#8b8fa8', fontSize: 12 }}>{item.category || '—'}</Td>
                  <Td style={{ color: '#6366f1', fontSize: 12 }}>{Math.round(item.match_score * 100)}%</Td>
                </Tr>
              ))}
            </Table>
          </Card>
        )
      ) : (
        comparisonQuery.isLoading ? <Loading text="Comparing competitors..." /> : (
          <Card>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 14 }}>
              <div>
                <h3 style={{ fontWeight: 800, fontSize: 16 }}>{comparison?.target?.title || selected.title}</h3>
                <div style={{ fontSize: 13, color: '#8b8fa8' }}>
                  Found on {comparison?.total_matches || foundRows.length} of {rows.length} active competitors
                </div>
              </div>
              {foundRows[0]?.product && (
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 12, color: '#8b8fa8' }}>Lowest price</div>
                  <div style={{ fontWeight: 900, color: '#22c55e', fontSize: 20 }}>
                    {formatPrice(foundRows[0].product.current_price, foundRows[0].product.currency)}
                  </div>
                </div>
              )}
            </div>

            <Table headers={['Competitor', 'Matched Item', 'Category', 'Price', 'Stock', 'Last Checked', '']}>
              {rows.map((row: any) => {
                const p = row.product
                return (
                  <Tr key={row.competitor_id} onClick={() => p && navigate(`/products/${p.id}`)}>
                    <Td>
                      <span style={{ background: '#2d3048', padding: '3px 8px', borderRadius: 6, fontSize: 12, color: '#c4c6d8' }}>
                        {row.competitor_name}
                      </span>
                    </Td>
                    <Td>
                      {p ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          {p.image_url && (
                            <img src={p.image_url} alt="" style={{ width: 36, height: 36, objectFit: 'cover', borderRadius: 4 }}
                              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                          )}
                          <div>
                            <div style={{ color: '#e4e4f0', fontWeight: 600 }}>{p.title}</div>
                            <div style={{ color: '#6366f1', fontSize: 11 }}>{Math.round(row.match_score * 100)}% match</div>
                          </div>
                        </div>
                      ) : <span style={{ color: '#6b7280' }}>Not found</span>}
                    </Td>
                    <Td style={{ color: '#8b8fa8', fontSize: 12 }}>{p?.category || '—'}</Td>
                    <Td style={{ fontWeight: 800, color: p ? '#e4e4f0' : '#6b7280' }}>{p ? formatPrice(p.current_price, p.currency) : '—'}</Td>
                    <Td>{p ? <StockBadge status={p.stock_status} /> : '—'}</Td>
                    <Td style={{ color: '#8b8fa8', fontSize: 13 }}>{p ? timeAgo(p.last_checked_at) : '—'}</Td>
                    <Td>
                      {p && (
                        <a href={p.url} target="_blank" rel="noopener noreferrer"
                          onClick={e => e.stopPropagation()}
                          style={{ color: '#6366f1', fontSize: 12, textDecoration: 'none' }}>↗</a>
                      )}
                    </Td>
                  </Tr>
                )
              })}
            </Table>
          </Card>
        )
      )}
    </div>
  )
}
