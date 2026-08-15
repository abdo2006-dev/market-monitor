import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Clock3,
  ExternalLink,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Store,
} from 'lucide-react'
import {
  compareProduct,
  getSearchSuggestions,
  getSyncRequest,
  scanAllCompetitors,
} from '../lib/api'
import type {
  CompareRow,
  SearchCoverageState,
  SearchCurrencySummary,
  SearchSuggestion,
  SyncRequestStatus,
} from '../lib/types'
import { Button, EmptyState, ErrorState, StockBadge } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatDate, formatPrice } from '../lib/utils'
import './MarketSearch.css'

const TERMINAL_SYNC_STATES = new Set(['success', 'partial', 'failed'])
const PREVIEW_DEMO_MODE = import.meta.env.VITE_PREVIEW_DEMO_MODE === 'true'

const COVERAGE_COPY: Record<SearchCoverageState, { label: string; tone: string }> = {
  current_complete: { label: 'Complete coverage', tone: 'good' },
  partial: { label: 'Partial catalog', tone: 'warn' },
  suspicious_empty: { label: 'Suspicious empty', tone: 'danger' },
  failed: { label: 'Sync failed', tone: 'danger' },
  stale: { label: 'Older coverage', tone: 'muted' },
  unknown: { label: 'Legacy / unknown', tone: 'muted' },
}

function useDebouncedValue(value: string, delay: number) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}

function readableAge(seconds: number | null) {
  if (seconds == null) return 'time unknown'
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`
  const days = Math.floor(seconds / 86400)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

function errorMessage(error: unknown) {
  if (error && typeof error === 'object' && 'message' in error) return String(error.message)
  return 'Something went wrong while loading market data.'
}

export default function MarketSearchPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialQuery = searchParams.get('q') || ''
  const [inputValue, setInputValue] = useState(initialQuery)
  const [selected, setSelected] = useState<SearchSuggestion | null>(null)
  const [suggestionsOpen, setSuggestionsOpen] = useState(Boolean(initialQuery))
  const [activeSuggestion, setActiveSuggestion] = useState(0)
  const [syncRequestId, setSyncRequestId] = useState<string | null>(null)
  const handledSyncRequest = useRef<string | null>(null)
  const debouncedQuery = useDebouncedValue(inputValue.trim(), 250)

  const suggestionsQuery = useQuery({
    queryKey: ['search-suggestions', debouncedQuery],
    queryFn: () => getSearchSuggestions({ q: debouncedQuery, limit: 12 }),
    enabled: debouncedQuery.length >= 2 && !selected,
    staleTime: 30_000,
  })
  const suggestions = suggestionsQuery.data?.items || []

  const comparisonQuery = useQuery({
    queryKey: ['compare-product', selected?.representative_product_id],
    queryFn: () => compareProduct({ product_id: selected!.representative_product_id }),
    enabled: Boolean(selected),
    staleTime: 15_000,
  })

  const syncMutation = useMutation({
    mutationFn: scanAllCompetitors,
    onSuccess: request => {
      handledSyncRequest.current = null
      setSyncRequestId(request.request_id)
    },
  })
  const syncRequestQuery = useQuery({
    queryKey: ['sync-request', syncRequestId],
    queryFn: () => getSyncRequest(syncRequestId!),
    enabled: Boolean(syncRequestId),
    refetchInterval: query => {
      const request = query.state.data as SyncRequestStatus | undefined
      return request && TERMINAL_SYNC_STATES.has(request.status) ? false : 2_000
    },
  })
  const syncRequest = syncRequestQuery.data || syncMutation.data
  const syncIsActive = syncMutation.isPending || Boolean(
    syncRequest && !TERMINAL_SYNC_STATES.has(syncRequest.status),
  )

  useEffect(() => {
    if (!syncRequest || !TERMINAL_SYNC_STATES.has(syncRequest.status)) return
    if (handledSyncRequest.current === syncRequest.request_id) return
    handledSyncRequest.current = syncRequest.request_id
    void queryClient.invalidateQueries({ queryKey: ['compare-product'] })
    void queryClient.invalidateQueries({ queryKey: ['search-suggestions'] })
  }, [queryClient, syncRequest])

  useEffect(() => setActiveSuggestion(0), [debouncedQuery])

  const chooseSuggestion = (suggestion: SearchSuggestion) => {
    setSelected(suggestion)
    setInputValue(suggestion.title)
    setSuggestionsOpen(false)
    setSearchParams({ q: suggestion.title })
  }

  const submitSearch = () => {
    const query = inputValue.trim()
    if (!query) return
    setSearchParams({ q: query })
    setSuggestionsOpen(true)
    if (debouncedQuery === query && suggestions.length > 0) {
      chooseSuggestion(suggestions[activeSuggestion] || suggestions[0])
    }
  }

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setSuggestionsOpen(true)
      setActiveSuggestion(index => Math.min(index + 1, Math.max(0, suggestions.length - 1)))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveSuggestion(index => Math.max(0, index - 1))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      submitSearch()
    } else if (event.key === 'Escape') {
      setSuggestionsOpen(false)
    }
  }

  const comparison = comparisonQuery.data
  const summary = comparison?.market_summary
  const syncMessage = syncStatusCopy(syncRequest, syncMutation.isError)

  return (
    <div className="market-search-page">
      <PageHeader
        title="Market Search"
        subtitle="Compare the market with observation age and catalog coverage in view."
        action={selected ? (
          <Button
            variant="secondary"
            onClick={() => syncMutation.mutate()}
            disabled={syncIsActive}
            aria-label="Refresh market data"
          >
            <span className="button-content">
              <RefreshCw size={15} className={syncIsActive ? 'spin' : ''} />
              {syncIsActive ? 'Sync in progress' : 'Refresh market data'}
            </span>
          </Button>
        ) : undefined}
      />

      {PREVIEW_DEMO_MODE && (
        <div className="preview-demo-notice" role="status">
          <strong>Preview data is deterministic.</strong>{' '}
          “Refresh market data” records a durable request in the isolated preview database,
          but no external Sync runner is connected, so accepted work remains queued.
        </div>
      )}

      <section className={`search-hero ${selected ? 'search-hero--compact' : ''}`}>
        {!selected && (
          <div className="search-kicker"><Sparkles size={14} /> Market intelligence</div>
        )}
        <div className="search-copy">
          <h2>{selected ? 'Compare another product' : 'What are you pricing today?'}</h2>
          {!selected && <p>Find the logical item once, then see every competitor with its evidence attached.</p>}
        </div>
        <div className="search-box" role="search">
          <Search size={20} aria-hidden="true" />
          <input
            value={inputValue}
            onChange={event => {
              setInputValue(event.target.value)
              setSelected(null)
              setSuggestionsOpen(true)
            }}
            onFocus={() => setSuggestionsOpen(true)}
            onKeyDown={onInputKeyDown}
            placeholder="Try Batwing, Chroma Luger, or Noobini…"
            role="combobox"
            aria-label="Search products"
            aria-autocomplete="list"
            aria-expanded={suggestionsOpen && debouncedQuery.length >= 2}
            aria-controls="market-search-suggestions"
            aria-activedescendant={suggestions[activeSuggestion] ? `suggestion-${activeSuggestion}` : undefined}
          />
          {suggestionsQuery.isFetching && <span className="search-spinner" aria-label="Loading suggestions" />}
          <button type="button" onClick={submitSearch}>Search</button>
        </div>

        {!selected && suggestionsOpen && debouncedQuery.length >= 2 && (
          <div className="suggestions-panel" id="market-search-suggestions" role="listbox">
            {suggestionsQuery.isLoading ? (
              <SuggestionSkeleton />
            ) : suggestionsQuery.isError ? (
              <div className="suggestion-message suggestion-message--error">
                Could not load suggestions. <button onClick={() => suggestionsQuery.refetch()}>Try again</button>
              </div>
            ) : suggestions.length === 0 ? (
              <div className="suggestion-message">
                No products matched “{debouncedQuery}”. Try a shorter name or different spelling.
              </div>
            ) : suggestions.map((suggestion, index) => (
              <button
                id={`suggestion-${index}`}
                type="button"
                role="option"
                aria-selected={activeSuggestion === index}
                className={`suggestion-row ${activeSuggestion === index ? 'is-active' : ''}`}
                key={suggestion.representative_product_id}
                onMouseEnter={() => setActiveSuggestion(index)}
                onClick={() => chooseSuggestion(suggestion)}
              >
                <ProductThumb src={suggestion.image_url} title={suggestion.title} />
                <span className="suggestion-identity">
                  <strong>{suggestion.title}</strong>
                  <small>{suggestion.category || 'Uncategorized'} · {suggestion.competitors_count} competitor{suggestion.competitors_count === 1 ? '' : 's'}</small>
                </span>
                <span className="suggestion-price">
                  <small>Lowest observed</small>
                  <strong>{suggestion.currency === 'MULTI' ? 'Multiple currencies' : formatPrice(suggestion.best_price, suggestion.currency)}</strong>
                </span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
            ))}
            {suggestionsQuery.data?.candidate_limit_reached && (
              <div className="suggestion-limit-note">
                Showing the best matches from {suggestionsQuery.data.candidates_considered.toLocaleString()} recent candidates. Add another word to narrow the market.
              </div>
            )}
          </div>
        )}
      </section>

      {syncMessage && (
        <div className={`sync-notice sync-notice--${syncMessage.tone}`} role="status">
          <RefreshCw size={16} className={syncIsActive ? 'spin' : ''} />
          <div><strong>{syncMessage.title}</strong><span>{syncMessage.detail}</span></div>
        </div>
      )}

      {!selected ? (
        debouncedQuery.length < 2 && (
          <section className="search-empty">
            <div className="empty-orbit"><Search size={30} /></div>
            <h3>One search, a clearer market</h3>
            <p>Prices stay visible even when evidence is old or incomplete. Reliable ranges use only current, complete catalog observations.</p>
            <div className="empty-principles">
              <span><ShieldCheck size={15} /> Coverage-aware prices</span>
              <span><Clock3 size={15} /> Actual observation age</span>
              <span><Store size={15} /> One row per competitor</span>
            </div>
          </section>
        )
      ) : comparisonQuery.isLoading ? (
        <ResultsSkeleton />
      ) : comparisonQuery.isError ? (
        <section className="search-state-card">
          <ErrorState message={errorMessage(comparisonQuery.error)} />
          <Button variant="secondary" onClick={() => comparisonQuery.refetch()}>Try comparison again</Button>
        </section>
      ) : comparison && summary ? (
        <ResultsView
          title={comparison.target?.title || selected.title}
          category={comparison.identity?.collection_label || comparison.target?.category}
          rows={comparison.items}
          summaries={summary.currencies}
          matched={summary.competitors_carrying}
          trustworthy={summary.trustworthy_current}
          degraded={summary.degraded_or_unknown}
          noReliable={summary.no_reliable_prices}
          onOpenProduct={id => navigate(`/products/${id}`)}
        />
      ) : (
        <section className="search-state-card">
          <EmptyState icon="◌" title="No comparison found" description="Try a different spelling or choose another suggestion." />
        </section>
      )}
    </div>
  )
}

function ResultsView({
  title,
  category,
  rows,
  summaries,
  matched,
  trustworthy,
  degraded,
  noReliable,
  onOpenProduct,
}: {
  title: string
  category?: string | null
  rows: CompareRow[]
  summaries: SearchCurrencySummary[]
  matched: number
  trustworthy: number
  degraded: number
  noReliable: boolean
  onOpenProduct: (id: number) => void
}) {
  return (
    <div className="results-stack">
      <section className="result-heading">
        <div>
          <span className="eyebrow">Matched market</span>
          <h2>{title}</h2>
          <p>{category || 'Uncategorized'} · Found at {matched} competitor{matched === 1 ? '' : 's'}</p>
        </div>
        <div className="coverage-counts" aria-label="Coverage summary">
          <span><strong>{trustworthy}</strong> trustworthy/current</span>
          <span><strong>{degraded}</strong> degraded or unknown</span>
        </div>
      </section>

      {noReliable && (
        <div className="market-warning" role="alert">
          <AlertTriangle size={19} />
          <div>
            <strong>No reliable current price is available.</strong>
            <span>Stored observations are still shown below, but none qualify for the current market range.</span>
          </div>
        </div>
      )}

      {summaries.length > 0 ? summaries.map(summary => (
        <MarketSummary key={summary.currency} summary={summary} />
      )) : (
        <div className="market-warning market-warning--neutral">
          <AlertTriangle size={18} /><span>No matched competitor has a comparable stored price.</span>
        </div>
      )}

      <section className="competitor-section">
        <div className="section-heading">
          <div><span className="eyebrow">Competitor comparison</span><h3>Every listing, with its evidence</h3></div>
          <span>{rows.length} active competitor{rows.length === 1 ? '' : 's'}</span>
        </div>
        <div className="competitor-list">
          {rows.map(row => (
            <CompetitorResult key={row.competitor_id} row={row} onOpenProduct={onOpenProduct} />
          ))}
        </div>
      </section>
    </div>
  )
}

function MarketSummary({ summary }: { summary: SearchCurrencySummary }) {
  const observedDiffers = summary.lowest_reliable_price == null
    || summary.lowest_observed_price !== summary.lowest_reliable_price
  return (
    <section className="market-summary" aria-label={`${summary.currency} market summary`}>
      <div className="summary-title">
        <div><span className="eyebrow">{summary.currency} market</span><h3>Trustworthy price range</h3></div>
        <span>{summary.reliable_price_count} of {summary.observed_price_count} prices qualify</span>
      </div>
      <div className="summary-grid">
        <SummaryMetric
          label="Lowest reliable"
          value={summary.lowest_reliable_price == null ? 'Not available' : formatPrice(summary.lowest_reliable_price, summary.currency)}
          detail={summary.lowest_reliable_competitor_name || 'No current complete offer'}
          accent="good"
        />
        <SummaryMetric
          label="Lowest observed"
          value={formatPrice(summary.lowest_observed_price, summary.currency)}
          detail={`${summary.lowest_observed_competitor_name || 'Unknown seller'}${observedDiffers ? ' · verify evidence' : ' · also reliable'}`}
          accent={observedDiffers ? 'warn' : 'neutral'}
        />
        <SummaryMetric label="Reliable median" value={formatPrice(summary.median_reliable_price, summary.currency)} detail="Middle of comparable current prices" />
        <SummaryMetric label="Reliable high" value={formatPrice(summary.highest_reliable_price, summary.currency)} detail="Top of the comparable range" />
      </div>
    </section>
  )
}

function SummaryMetric({ label, value, detail, accent = 'neutral' }: {
  label: string; value: string; detail: string; accent?: 'good' | 'warn' | 'neutral'
}) {
  return (
    <div className={`summary-metric summary-metric--${accent}`}>
      <span>{label}</span><strong>{value}</strong><small>{detail}</small>
    </div>
  )
}

function CompetitorResult({ row, onOpenProduct }: { row: CompareRow; onOpenProduct: (id: number) => void }) {
  const product = row.product
  const coverage = COVERAGE_COPY[row.trust.coverage_state]
  const difference = row.difference_from_reliable_low
  return (
    <article className={`competitor-result ${row.trust.reliable ? 'is-reliable' : 'is-degraded'}`}>
      <div className="competitor-main">
        <div className="competitor-name">
          <span className="store-mark"><Store size={17} /></span>
          <div><strong>{row.competitor_name}</strong>{product && <small>{product.title}</small>}</div>
        </div>
        <div className="trust-cluster">
          {row.trust.active_sync && <span className="trust-pill trust-pill--sync"><RefreshCw size={12} className="spin" /> Syncing now</span>}
          <span className={`trust-pill trust-pill--${coverage.tone}`}>
            {row.trust.coverage_state === 'current_complete' ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
            {coverage.label}
          </span>
        </div>
      </div>

      {product ? (
        <div className="competitor-body">
          <ProductThumb src={product.image_url} title={product.title} />
          <div className="listing-context">
            <StockBadge status={product.stock_status} />
            <span>Observed <strong title={formatDate(row.trust.product_observed_at)}>{readableAge(row.trust.product_observation_age_seconds)}</strong></span>
            <span>Complete catalog <strong title={formatDate(row.trust.latest_complete_at)}>{readableAge(row.trust.complete_coverage_age_seconds)}</strong></span>
          </div>
          <div className="listing-price">
            <strong>{formatPrice(product.current_price, product.currency)}</strong>
            {row.trust.reliable && difference === 0 ? (
              <span className="price-reference price-reference--best"><ShieldCheck size={13} /> Reliable low</span>
            ) : difference != null ? (
              <span className="price-reference">+{formatPrice(difference, product.currency)} vs reliable low</span>
            ) : (
              <span className="price-reference">Observed price only</span>
            )}
          </div>
          <div className="listing-actions">
            <button type="button" onClick={() => onOpenProduct(product.id)}>Details</button>
            <a href={product.url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${product.title} at ${row.competitor_name}`}><ExternalLink size={16} /></a>
          </div>
        </div>
      ) : (
        <div className="no-listing"><span>No matched active listing</span><small>Coverage evidence still applies to this competitor.</small></div>
      )}

      {(row.trust.warning || row.price_change) && (
        <div className="result-context">
          {row.trust.warning && <p><AlertTriangle size={14} /> {row.trust.warning}</p>}
          {row.price_change && (
            <p className={`change-context change-context--${row.price_change.direction}`}>
              {row.price_change.direction === 'decrease' ? <ArrowDownRight size={15} /> : <ArrowUpRight size={15} />}
              Price {row.price_change.direction === 'decrease' ? 'fell' : 'rose'} from {formatPrice(row.price_change.previous_price, row.price_change.currency)} by {formatPrice(row.price_change.amount, row.price_change.currency)}
              {row.price_change.percentage != null ? ` (${row.price_change.percentage.toFixed(1)}%)` : ''} · {formatDate(row.price_change.changed_at)}
            </p>
          )}
        </div>
      )}

      <details className="evidence-details">
        <summary>Evidence details</summary>
        <div>
          <span>Required Cairo cycle <strong>{row.trust.required_cycle_date}</strong></span>
          <span>Current acquisition <strong>{row.trust.current_completeness.replace('_', ' ')}</strong></span>
          <span>Producing run <strong>{row.trust.producing_run ? `#${row.trust.producing_run.run_id}` : 'Legacy / unknown'}</strong></span>
          <span>Latest complete <strong>{row.trust.latest_complete_run ? `#${row.trust.latest_complete_run.run_id} · ` : ''}{formatDate(row.trust.latest_complete_at)}</strong></span>
          {row.trust.latest_partial_run && <span>Latest partial <strong>#{row.trust.latest_partial_run.run_id} · {formatDate(row.trust.latest_partial_at)}</strong></span>}
          {row.trust.last_failed_at && <span>Latest failure <strong>{row.trust.latest_failed_run ? `#${row.trust.latest_failed_run.run_id} · ` : ''}{formatDate(row.trust.last_failed_at)}</strong></span>}
        </div>
      </details>
    </article>
  )
}

function ProductThumb({ src, title }: { src?: string | null; title: string }) {
  if (!src) return <span className="product-thumb product-thumb--empty"><Store size={18} /></span>
  return <img className="product-thumb" src={src} alt="" onError={event => { event.currentTarget.style.visibility = 'hidden' }} title={title} />
}

function SuggestionSkeleton() {
  return <div className="suggestion-skeleton" aria-label="Loading product suggestions">{[0, 1, 2].map(item => <span key={item} />)}</div>
}

function ResultsSkeleton() {
  return <div className="results-skeleton" aria-label="Loading competitor comparison"><span /><div><span /><span /><span /><span /></div><span /><span /><span /></div>
}

function syncStatusCopy(request?: SyncRequestStatus, mutationFailed = false) {
  if (mutationFailed) return { tone: 'danger', title: 'Sync request failed', detail: 'No refresh was accepted. Existing Search data is unchanged.' }
  if (!request) return null
  if (request.status === 'queued') return { tone: 'info', title: 'Sync request accepted', detail: 'Market data has not refreshed yet; the durable request is waiting for a worker.' }
  if (request.status === 'running' || request.status === 'retrying') return { tone: 'info', title: 'Market data is updating', detail: 'Search continues to show the prior observations until reconciliation completes.' }
  if (request.status === 'success') return { tone: 'good', title: 'Sync completed', detail: 'Search data was reloaded from the completed catalog observations.' }
  if (request.status === 'partial') return { tone: 'warn', title: 'Sync completed with degraded coverage', detail: 'Observed products were reloaded, but incomplete catalogs remain clearly marked.' }
  return { tone: 'danger', title: 'Sync did not complete', detail: 'Stored observations remain available and are marked with their prior evidence.' }
}
