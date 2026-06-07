import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '../components/layout/Sidebar'
import { Button, Card, ErrorState, Input, Loading, Select } from '../components/ui'
import { collectionPricesExportUrl, getCompetitors } from '../lib/api'

export default function ExportsPage() {
  const [competitorId, setCompetitorId] = useState('')
  const [collectionUrl, setCollectionUrl] = useState('')
  const [format, setFormat] = useState('jsonl')
  const [maxPages, setMaxPages] = useState(5)
  const [error, setError] = useState('')

  const { data: competitors = [], isLoading, error: loadError } = useQuery({
    queryKey: ['competitors'],
    queryFn: getCompetitors,
  })

  const selectedCompetitor = useMemo(
    () => competitors.find((item: any) => String(item.id) === competitorId),
    [competitors, competitorId],
  )

  const exportUrl = competitorId && collectionUrl.trim()
    ? collectionPricesExportUrl({
      competitor_id: competitorId,
      collection_url: collectionUrl.trim(),
      format,
      max_pages: maxPages,
    })
    : ''

  const handleDownload = () => {
    setError('')
    if (!competitorId) {
      setError('Choose a competitor first.')
      return
    }
    if (!collectionUrl.trim()) {
      setError('Paste a collection URL first.')
      return
    }
    try {
      const collectionHost = new URL(collectionUrl.trim()).hostname.replace(/^www\./, '')
      const competitorHost = selectedCompetitor ? new URL(selectedCompetitor.base_url).hostname.replace(/^www\./, '') : ''
      if (competitorHost && collectionHost !== competitorHost) {
        setError('The collection URL must belong to the selected competitor.')
        return
      }
    } catch {
      setError('Use a full collection URL starting with https://.')
      return
    }
    window.location.href = exportUrl
  }

  if (isLoading) return <div style={{ padding: '2rem' }}><Loading /></div>
  if (loadError) return <div style={{ padding: '2rem' }}><ErrorState message="Failed to load competitors." /></div>

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader
        title="Exports"
        subtitle="Download a competitor collection as clean price data."
      />

      <Card style={{ maxWidth: 820 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 180px', gap: 14, marginBottom: 14 }}>
          <Select label="Competitor" value={competitorId} onChange={e => setCompetitorId(e.target.value)}>
            <option value="">Choose competitor</option>
            {competitors.map((competitor: any) => (
              <option key={competitor.id} value={competitor.id}>{competitor.name}</option>
            ))}
          </Select>

          <Select label="Format" value={format} onChange={e => setFormat(e.target.value)}>
            <option value="jsonl">JSONL</option>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </Select>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 140px', gap: 14, marginBottom: 16 }}>
          <Input
            label="Collection URL"
            value={collectionUrl}
            onChange={e => setCollectionUrl(e.target.value)}
            placeholder="https://competitor.com/collections/steal-a-brainrot"
          />
          <Input
            label="Max Pages"
            type="number"
            min={1}
            max={20}
            value={maxPages}
            onChange={e => setMaxPages(Number(e.target.value))}
          />
        </div>

        {selectedCompetitor && (
          <div style={{ color: '#8b8fa8', fontSize: 13, marginBottom: 14 }}>
            Selected site: <span style={{ color: '#c4c6d8' }}>{selectedCompetitor.base_url}</span>
          </div>
        )}

        {error && (
          <div style={{
            background: '#ef444422',
            border: '1px solid #ef444455',
            color: '#fca5a5',
            borderRadius: 8,
            padding: '9px 12px',
            fontSize: 13,
            marginBottom: 14,
          }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <Button onClick={handleDownload}>Download Prices</Button>
          {exportUrl && (
            <a href={exportUrl} style={{ color: '#6366f1', fontSize: 13 }} target="_blank" rel="noopener noreferrer">
              Open export URL
            </a>
          )}
        </div>

        <div style={{ marginTop: 18, color: '#8b8fa8', fontSize: 13, lineHeight: 1.6 }}>
          JSONL is recommended for LLM workflows: one product object per line. CSV is better for spreadsheets.
          The export scrapes the current collection live and does not alter your saved product database.
        </div>
      </Card>
    </div>
  )
}
