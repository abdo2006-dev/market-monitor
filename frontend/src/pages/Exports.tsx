import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '../components/layout/Sidebar'
import { Button, Card, ErrorState, Input, Loading, Select } from '../components/ui'
import { getCompetitors, prepareCollectionExport } from '../lib/api'
import type {
  CollectionExportDownload,
  CollectionExportFailure,
  Competitor,
  ExportFormat,
  ExportMode,
} from '../lib/types'
import { formatDate, timeAgo } from '../lib/utils'
import './Exports.css'

type ExportState = 'idle' | 'preparing' | 'ready' | 'failed'

function readExportFailure(error: unknown): CollectionExportFailure | null {
  const data = (error as { response?: { data?: unknown } })?.response?.data
  if (!data || typeof data !== 'object') return null
  if (typeof (data as Blob).text === 'function') {
    // Axios returns the error body as a Blob because the download request has
    // responseType=blob. Parse only the safe JSON error contract.
    return null
  }
  const detail = (data as { detail?: unknown }).detail
  return isCollectionExportFailure(detail) ? detail : null
}

async function parseBlobFailure(error: unknown): Promise<CollectionExportFailure | null> {
  const data = (error as { response?: { data?: unknown } })?.response?.data
  if (!data || typeof (data as Blob).text !== 'function') {
    return readExportFailure(error)
  }
  try {
    const body = JSON.parse(await (data as Blob).text())
    return isCollectionExportFailure(body?.detail) ? body.detail : null
  } catch {
    return null
  }
}

function isCollectionExportFailure(value: unknown): value is CollectionExportFailure {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<CollectionExportFailure>
  return (candidate.code === 'live_acquisition_failed' || candidate.code === 'cached_export_unavailable')
    && typeof candidate.message === 'string'
    && Boolean(candidate.provenance)
}

function downloadPreparedFile(prepared: CollectionExportDownload) {
  const url = URL.createObjectURL(prepared.blob)
  const link = document.createElement('a')
  link.href = url
  link.download = prepared.filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function completenessLabel(value: CollectionExportDownload['provenance']['completeness']) {
  return {
    complete: 'Complete catalog',
    partial: 'Partial live data',
    suspicious_empty: 'Suspicious empty result',
    failed: 'Live acquisition failed',
    unknown: 'Unknown coverage',
  }[value]
}

export default function ExportsPage() {
  const [competitorId, setCompetitorId] = useState('')
  const [collectionUrl, setCollectionUrl] = useState('')
  const [format, setFormat] = useState<ExportFormat>('jsonl')
  const [maxPages, setMaxPages] = useState(5)
  const [mode, setMode] = useState<ExportMode>('live')
  const [state, setState] = useState<ExportState>('idle')
  const [error, setError] = useState('')
  const [failure, setFailure] = useState<CollectionExportFailure | null>(null)
  const [prepared, setPrepared] = useState<CollectionExportDownload | null>(null)

  const { data: competitors = [], isLoading, error: loadError } = useQuery({
    queryKey: ['competitors'],
    queryFn: getCompetitors,
  })

  const selectedCompetitor = useMemo(
    () => competitors.find((item: Competitor) => String(item.id) === competitorId),
    [competitors, competitorId],
  )

  const resetPrepared = () => {
    setPrepared(null)
    setFailure(null)
    if (state !== 'preparing') setState('idle')
  }

  const validate = () => {
    if (!competitorId) return 'Choose a competitor first.'
    if (!collectionUrl.trim()) return 'Paste a collection URL first.'
    try {
      const collection = new URL(collectionUrl.trim())
      const competitor = selectedCompetitor && new URL(selectedCompetitor.base_url)
      if (!['https:', 'http:'].includes(collection.protocol)) {
        return 'Use a full collection URL starting with https://.'
      }
      if (competitor && collection.hostname.replace(/^www\./, '') !== competitor.hostname.replace(/^www\./, '')) {
        return 'The collection URL must belong to the selected competitor.'
      }
    } catch {
      return 'Use a full collection URL starting with https://.'
    }
    return ''
  }

  const prepare = async (requestedMode = mode) => {
    const validation = validate()
    setError(validation)
    if (validation) return
    setState('preparing')
    setPrepared(null)
    setFailure(null)
    try {
      const result = await prepareCollectionExport({
        competitor_id: competitorId,
        collection_url: collectionUrl.trim(),
        format,
        max_pages: maxPages,
        mode: requestedMode,
      })
      setMode(requestedMode)
      setPrepared(result)
      setState('ready')
    } catch (requestError) {
      const structured = await parseBlobFailure(requestError)
      setFailure(structured)
      setError(structured?.message || 'The export could not be prepared. Check the collection URL and retry.')
      setState('failed')
    }
  }

  if (isLoading) return <div style={{ padding: '2rem' }}><Loading text="Loading export sources…" /></div>
  if (loadError) return <div style={{ padding: '2rem' }}><ErrorState message="Failed to load competitors." /></div>

  const provenance = prepared?.provenance
  return (
    <div className="exports-page">
      <PageHeader
        title="Collection exports"
        subtitle="Prepare a truthful collection download without changing your saved market data."
      />

      <div className="exports-layout">
        <Card className="exports-form-card">
          <fieldset className="exports-source" disabled={state === 'preparing'}>
            <legend>Export source</legend>
            <label className={`exports-mode ${mode === 'live' ? 'selected' : ''}`}>
              <input type="radio" name="export-mode" value="live" checked={mode === 'live'} onChange={() => { setMode('live'); resetPrepared() }} />
              <span><strong>Live current collection</strong><small>Acquire this collection now. A failed or empty result never becomes stored data.</small></span>
            </label>
            <label className={`exports-mode ${mode === 'cached' ? 'selected' : ''}`}>
              <input type="radio" name="export-mode" value="cached" checked={mode === 'cached'} onChange={() => { setMode('cached'); resetPrepared() }} />
              <span><strong>Latest stored data</strong><small>Use active stored rows intentionally. Their observation ages and coverage are disclosed.</small></span>
            </label>
          </fieldset>

          <div className="exports-grid exports-grid-top">
            <Select id="export-competitor" label="Competitor" value={competitorId} onChange={e => { setCompetitorId(e.target.value); resetPrepared() }} disabled={state === 'preparing'}>
              <option value="">Choose competitor</option>
              {competitors.map((competitor: Competitor) => (
                <option key={competitor.id} value={competitor.id}>{competitor.name}</option>
              ))}
            </Select>
            <Select id="export-format" label="Format" value={format} onChange={e => { setFormat(e.target.value as ExportFormat); resetPrepared() }} disabled={state === 'preparing'}>
              <option value="jsonl">JSONL</option>
              <option value="csv">CSV</option>
              <option value="json">JSON</option>
            </Select>
          </div>

          <div className="exports-grid exports-grid-bottom">
            <Input
              label="Collection URL"
              id="export-collection-url"
              value={collectionUrl}
              onChange={e => { setCollectionUrl(e.target.value); resetPrepared() }}
              placeholder="https://competitor.com/collections/steal-a-brainrot"
              disabled={state === 'preparing'}
            />
            <Input
              label="Max pages"
              id="export-max-pages"
              type="number"
              min={1}
              max={20}
              value={maxPages}
              onChange={e => { setMaxPages(Math.max(1, Math.min(20, Number(e.target.value) || 1))); resetPrepared() }}
              disabled={state === 'preparing'}
            />
          </div>

          {selectedCompetitor && <p className="exports-site">Selected site: <strong>{selectedCompetitor.base_url}</strong></p>}

          <div className="exports-actions">
            <Button onClick={() => prepare()} disabled={state === 'preparing'}>
              {state === 'preparing' ? 'Acquiring collection…' : 'Prepare export'}
            </Button>
            <span className="exports-format-help">JSONL suits LLM workflows; CSV suits spreadsheets.</span>
          </div>
        </Card>

        <aside className="exports-trust-card" aria-live="polite">
          {state === 'idle' && (
            <><h2>What happens next</h2><p>{mode === 'live'
              ? 'The collection is acquired now and checked for complete, partial, or suspicious-empty coverage.'
              : 'Only active stored rows matching this collection are prepared. No live request is made.'}</p></>
          )}
          {state === 'preparing' && (
            <><h2>Preparing export</h2><p>Acquiring and serializing your {mode === 'live' ? 'live collection' : 'stored collection'} data. This can take a few seconds.</p></>
          )}
          {state === 'failed' && (
            <>
              <h2>Live acquisition failed</h2>
              <p>{error}</p>
              {failure?.provenance.safe_reason && <p className="exports-muted">Reason: {failure.provenance.safe_reason}</p>}
              {mode === 'live' && <div className="exports-retry-actions">
                <Button variant="secondary" size="sm" onClick={() => prepare('live')}>Retry live</Button>
                <Button variant="ghost" size="sm" onClick={() => prepare('cached')}>Prepare latest stored data</Button>
              </div>}
            </>
          )}
          {prepared && provenance && (
            <>
              <div className={`exports-status ${provenance.completeness}`}>
                <span>{provenance.source === 'live' ? 'LIVE' : 'STORED'}</span>
                <strong>{completenessLabel(provenance.completeness)}</strong>
              </div>
              <h2>{provenance.products_count} products ready</h2>
              <p>{provenance.source === 'live'
                ? `${provenance.pages_fetched} page${provenance.pages_fetched === 1 ? '' : 's'} fetched${provenance.observation_completed_at ? ` · observed ${timeAgo(provenance.observation_completed_at)}` : ''}.`
                : `Stored observations range from ${formatDate(provenance.oldest_observed_at)} to ${formatDate(provenance.newest_observed_at)}.`}</p>
              {provenance.safe_reason && <p className="exports-warning">{provenance.safe_reason}</p>}
              {provenance.page_cap_reached && <p className="exports-warning">Page limit reached — unobserved products may exist.</p>}
              {provenance.source === 'cached' && <p className="exports-muted">{provenance.degraded_or_legacy_row_count} row(s) are not directly linked to the latest complete catalog.</p>}
              <Button onClick={() => downloadPreparedFile(prepared)}>
                Download {provenance.completeness === 'partial' ? 'partial ' : ''}{format.toUpperCase()} export
              </Button>
            </>
          )}
        </aside>
      </div>

      {error && state !== 'failed' && <div className="exports-error" role="alert">{error}</div>}
      <p className="exports-footer">Live exports are read-only: they do not create Sync runs, update product prices, or substitute cached rows. Use “Latest stored data” only when you intentionally want stored observations.</p>
    </div>
  )
}
