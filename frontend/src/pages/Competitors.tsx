import React, { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  MoreHorizontal,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import {
  createCompetitor,
  deleteCompetitor,
  getCompetitors,
  getSyncFreshness,
  getSyncRequest,
  scanAllCompetitors,
  scanNow,
  retrySyncDispatch,
  seedDefaultCompetitors,
  updateCompetitor,
} from '../lib/api'
import { Button, EmptyState, ErrorState, Loading } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatDate, timeAgo } from '../lib/utils'
import CompetitorForm from '../components/CompetitorForm'
import type { Competitor, CompetitorFreshness, CompetitorInput, SyncRequestStatus, SyncRunStatus } from '../lib/types'
import './Competitors.css'

type Filter = 'all' | 'attention' | 'healthy' | 'syncing' | 'failed' | 'inactive'

const ACTIVE_RUNS = new Set(['queued', 'running', 'retry_wait'])
const ISSUE_STATES = new Set(['failed', 'abandoned', 'partial', 'suspicious_empty', 'retrying', 'lease_expired'])
const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'attention', label: 'Needs attention' },
  { key: 'healthy', label: 'Healthy' },
  { key: 'syncing', label: 'Syncing' },
  { key: 'failed', label: 'Failed' },
  { key: 'inactive', label: 'Inactive' },
]

function latestEvidenceAt(fresh?: CompetitorFreshness) {
  const evidence = [fresh?.last_complete_at, fresh?.latest_partial_at, fresh?.last_failed_at]
    .filter((value): value is string => Boolean(value))
    .sort()
  return evidence[evidence.length - 1] || null
}

function displayRunState(run: SyncRunStatus | undefined, competitor: Competitor, fresh?: CompetitorFreshness) {
  if (!competitor.active) return 'inactive'
  if (run?.operator_state === 'waiting_for_runner') return 'waiting_for_runner'
  if (run?.operator_state === 'lease_expired') return 'lease_expired'
  if (run?.status === 'retry_wait') return 'retrying'
  if (run && run.completeness !== 'unknown' && run.completeness !== 'complete') return run.completeness
  if (run?.status) return run.status
  if (fresh?.coverage_complete) return 'complete'
  if (competitor.last_scan_status) return competitor.last_scan_status
  return 'unknown'
}

function stateLabel(state: string) {
  return ({
    suspicious_empty: 'Suspicious empty',
    retry_wait: 'Retrying',
    stale_skipped: 'Stale skipped',
    waiting_for_runner: 'Waiting for runner',
    lease_expired: 'Lease expired',
  } as Record<string, string>)[state] || state.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase())
}

function StateIndicator({ state }: { state: string }) {
  const tone = ['success', 'complete'].includes(state) ? 'good'
    : ['queued', 'running', 'waiting_for_runner'].includes(state) ? 'info'
      : ['partial', 'suspicious_empty', 'retrying', 'lease_expired'].includes(state) ? 'warn'
        : ['failed', 'abandoned'].includes(state) ? 'danger' : 'muted'
  const Icon = tone === 'good' ? CheckCircle2 : tone === 'danger' ? AlertTriangle : tone === 'info' ? RefreshCw : Clock3
  return <span className={`sync-state sync-state--${tone}`}><Icon size={12} className={state === 'running' ? 'spin' : ''} />{stateLabel(state)}</span>
}

function requestRunnerLabel(request: SyncRequestStatus) {
  return ({
    awaiting_dispatch: 'Queued · awaiting dispatch',
    dispatched: 'Queued · runner notified',
    waiting_for_runner: 'Queued · waiting for runner',
    dispatch_recovery: 'Queued · dispatch needs recovery',
    running: 'Running',
    retry_wait: 'Retrying',
    terminal: stateLabel(request.status),
  } as Record<string, string>)[request.runner_state] || stateLabel(request.status)
}

export default function CompetitorsPage() {
  const qc = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Competitor | null>(null)
  const [scanningId, setScanningId] = useState<number | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const { data: competitors = [], isLoading, error } = useQuery({ queryKey: ['competitors'], queryFn: getCompetitors })
  const { data: freshness = [] } = useQuery({ queryKey: ['sync-freshness'], queryFn: getSyncFreshness, refetchInterval: 10000 })
  const { data: syncRequest } = useQuery({
    queryKey: ['sync-request', requestId],
    queryFn: () => getSyncRequest(requestId as string),
    enabled: requestId !== null,
    refetchInterval: 5000,
  })

  const createMut = useMutation({ mutationFn: createCompetitor, onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); setModalOpen(false) } })
  const updateMut = useMutation({ mutationFn: ({ id, data }: { id: number; data: Partial<CompetitorInput> }) => updateCompetitor(id, data), onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); qc.invalidateQueries({ queryKey: ['sync-freshness'] }); setEditing(null) } })
  const deleteMut = useMutation({ mutationFn: deleteCompetitor, onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }) })
  const seedMut = useMutation({ mutationFn: seedDefaultCompetitors, onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }) })
  const scanAllMut = useMutation({ mutationFn: scanAllCompetitors, onSuccess: result => { setRequestId(result.request_id); qc.invalidateQueries({ queryKey: ['sync-freshness'] }) } })
  const retryDispatchMut = useMutation({ mutationFn: retrySyncDispatch, onSuccess: result => { setRequestId(result.request_id); qc.invalidateQueries({ queryKey: ['sync-request', result.request_id] }) } })

  const handleScanNow = async (id: number) => {
    setScanningId(id)
    try {
      const result = await scanNow(id)
      setRequestId(result.request_id)
      qc.invalidateQueries({ queryKey: ['sync-freshness'] })
    } finally { setScanningId(null) }
  }

  const handleToggleActive = (competitor: Competitor) => {
    updateMut.mutate({ id: competitor.id, data: { active: !competitor.active } })
  }

  const visibleRequest: SyncRequestStatus | undefined = syncRequest || scanAllMut.data
  const freshnessByCompetitor = useMemo(() => new Map(freshness.map(row => [row.competitor_id, row])), [freshness])
  const requestRunsByCompetitor = useMemo(() => new Map((visibleRequest?.runs || []).map(run => [run.competitor_id, run])), [visibleRequest])

  if (isLoading) return <div className="page-state-wrap"><Loading text="Loading competitor readiness…" /></div>
  if (error) return <div className="page-state-wrap"><ErrorState message="Failed to load competitors." /></div>

  const activeCompetitors = competitors.filter(competitor => competitor.active)
  const previewDemoMode = import.meta.env.VITE_PREVIEW_DEMO_MODE === 'true'
  const scanControlsDisabled = scanAllMut.isPending || scanningId !== null
  const states = competitors.map(competitor => {
    const fresh = freshnessByCompetitor.get(competitor.id)
    const run = requestRunsByCompetitor.get(competitor.id) || fresh?.active_run || undefined
    return { competitor, fresh, run, state: displayRunState(run, competitor, fresh) }
  })
  const activeStates = states.filter(item => item.competitor.active)
  const activeNow = activeStates.filter(item => item.run && ACTIVE_RUNS.has(item.run.status)).length
  const needsAttention = activeStates.filter(item => ISSUE_STATES.has(item.state) || item.state === 'waiting_for_runner').length
  const healthy = activeStates.filter(item => item.fresh?.coverage_complete && !ISSUE_STATES.has(item.state) && item.state !== 'waiting_for_runner').length
  const inactive = states.length - activeStates.length
  const completeTimes = freshness.map(item => item.last_complete_at).filter((value): value is string => Boolean(value)).sort()
  const lastComplete = completeTimes[completeTimes.length - 1]
  const filteredStates = states.filter(item => {
    if (filter === 'all') return true
    if (filter === 'inactive') return !item.competitor.active
    if (filter === 'healthy') return item.competitor.active && item.fresh?.coverage_complete && !ISSUE_STATES.has(item.state) && item.state !== 'waiting_for_runner'
    if (filter === 'syncing') return item.run ? ACTIVE_RUNS.has(item.run.status) : false
    if (filter === 'failed') return ['failed', 'abandoned'].includes(item.state)
    return item.competitor.active && (ISSUE_STATES.has(item.state) || ['waiting_for_runner', 'unknown'].includes(item.state))
  })

  return (
    <div className="competitors-page">
      <PageHeader
        eyebrow="Market readiness"
        title="Competitor sync"
        subtitle="A compact operating view of catalog health, runner activity, and actionable failures."
        action={<Button variant="secondary" size="sm" onClick={() => setModalOpen(true)}><Plus size={14} /> Add competitor</Button>}
      />

      {previewDemoMode && (
        <div className="competitors-preview-notice" role="status">
          <ShieldCheck size={15} aria-hidden="true" />
          <span><strong>Protected preview.</strong> New requests remain queued because this isolated environment has no runner.</span>
        </div>
      )}

      <section className="readiness-bar" aria-label="Market sync readiness">
        <div className="readiness-copy">
          <strong>Market readiness</strong>
          <span><b className="is-good">{healthy} healthy</b> · <b className="is-danger">{needsAttention} attention</b> · {inactive} inactive</span>
          <small>Latest complete source {lastComplete ? timeAgo(lastComplete) : 'not established'}</small>
        </div>
        <div className="readiness-activity">{activeNow > 0 ? <><RefreshCw size={13} className="spin" /> {activeNow} active</> : 'No active runner'}</div>
        <Button size="sm" onClick={() => scanAllMut.mutate()} loading={scanAllMut.isPending} disabled={activeCompetitors.length === 0 || scanControlsDisabled}><RefreshCw size={14} /> Sync all</Button>
      </section>

      {visibleRequest && (
        <div className={`request-strip ${visibleRequest.needs_runner_recovery ? 'is-warning' : ''}`} role="status">
          <RefreshCw size={13} className={visibleRequest.runner_state === 'running' ? 'spin' : ''} />
          <strong>{requestRunnerLabel(visibleRequest)}</strong>
          <span>{visibleRequest.runs.length} run{visibleRequest.runs.length === 1 ? '' : 's'}</span>
          {visibleRequest.oldest_queued_seconds != null && <span>oldest queued {Math.max(1, Math.floor(visibleRequest.oldest_queued_seconds / 60))}m</span>}
          {visibleRequest.dispatch_error_category && <span>recovery reason: {stateLabel(visibleRequest.dispatch_error_category)}</span>}
          {visibleRequest.needs_runner_recovery && <button type="button" onClick={() => retryDispatchMut.mutate(visibleRequest.request_id)} disabled={retryDispatchMut.isPending}>{retryDispatchMut.isPending ? 'Retrying…' : 'Retry dispatch'}</button>}
        </div>
      )}

      <div className="competitor-filters" aria-label="Filter competitors">
        {FILTERS.map(item => <button type="button" className={filter === item.key ? 'is-active' : ''} aria-pressed={filter === item.key} onClick={() => setFilter(item.key)} key={item.key}>{item.label}</button>)}
        <span>{filteredStates.length} shown</span>
      </div>

      <section className="competitor-operations">
        {competitors.length === 0 ? (
          <div className="competitors-empty">
            <EmptyState title="No competitors configured" description="Add a competitor to begin building trustworthy market coverage." />
            <Button onClick={() => seedMut.mutate()} loading={seedMut.isPending}>Add starter stores</Button>
          </div>
        ) : (
          <div className="competitor-table" role="table" aria-label="Competitor sync operations">
            <div className="competitor-table-head" role="row">
              <span role="columnheader">Competitor</span><span role="columnheader">Status</span><span role="columnheader">Last success</span><span role="columnheader">Products</span><span role="columnheader">Coverage</span><span role="columnheader">Last error / note</span><span role="columnheader">Action</span>
            </div>
            {filteredStates.map(({ competitor, fresh, run, state }) => {
              const evidenceAt = latestEvidenceAt(fresh)
              const issue = ISSUE_STATES.has(state) || state === 'waiting_for_runner'
              const explanation = !competitor.active ? 'Storefront unavailable; historical products retained.'
                : run?.failure_reason || run?.completeness_reason || (
                  state === 'waiting_for_runner' ? 'Durable request is waiting for a runner.'
                    : state === 'failed' ? 'Latest Sync failed; stored coverage is unchanged.'
                      : state === 'unknown' ? 'No trustworthy V2 catalog lineage yet.' : '—'
                )
              const observedProducts = run && run.products_observed > 0 ? run.products_observed : fresh?.last_complete_run?.products_observed
              const productCount = observedProducts ?? fresh?.active_products ?? 0
              const coverage = !competitor.active ? 'Historical' : run && run.completeness !== 'unknown' ? stateLabel(run.completeness) : fresh?.coverage_complete ? 'Complete' : 'Unknown'
              const expanded = expandedId === competitor.id
              return (
                <article className={`competitor-row ${!competitor.active ? 'is-disabled' : ''} ${expanded ? 'is-expanded' : ''}`} role="row" key={competitor.id}>
                  <div className="competitor-identity" role="cell"><span className="competitor-monogram">{competitor.name.trim().charAt(0).toUpperCase()}</span><div><strong>{competitor.name}</strong><a href={competitor.base_url} target="_blank" rel="noreferrer">{competitor.base_url.replace(/^https?:\/\//, '')}</a></div></div>
                  <div role="cell"><StateIndicator state={state} /></div>
                  <div className="competitor-cell" role="cell"><strong>{fresh?.last_complete_at ? timeAgo(fresh.last_complete_at) : 'Never'}</strong><small>{fresh?.last_complete_at ? formatDate(fresh.last_complete_at) : 'No complete V2 run'}</small></div>
                  <div className="competitor-cell is-number" role="cell"><strong>{productCount.toLocaleString()}</strong><small>{observedProducts != null ? 'observed' : 'stored'}</small></div>
                  <div className="competitor-cell" role="cell"><strong>{coverage}</strong><small>{evidenceAt ? `evidence ${timeAgo(evidenceAt)}` : 'No evidence'}</small></div>
                  <div className={`competitor-note ${issue ? 'is-warning' : ''}`} role="cell" title={explanation}><span>{explanation}</span></div>
                  <div className="competitor-row-actions" role="cell">
                    {competitor.active && <Button size="sm" variant="secondary" onClick={() => handleScanNow(competitor.id)} loading={scanningId === competitor.id} disabled={scanControlsDisabled}><Play size={12} /> {['failed', 'abandoned'].includes(state) ? 'Retry' : 'Sync'}</Button>}
                    <button type="button" className="details-toggle" aria-label={`${expanded ? 'Hide' : 'Show'} details for ${competitor.name}`} aria-expanded={expanded} onClick={() => setExpandedId(expanded ? null : competitor.id)}><ChevronDown size={15} /></button>
                    <details className="competitor-actions-menu">
                      <summary aria-label={`More actions for ${competitor.name}`}><MoreHorizontal size={16} /></summary>
                      <div>
                        <button type="button" onClick={() => handleToggleActive(competitor)}>{competitor.active ? 'Mark inactive' : 'Enable source'}</button>
                        <button type="button" onClick={() => setEditing(competitor)}>Edit configuration</button>
                        <button type="button" className="is-danger" onClick={() => { if (confirm(`Delete ${competitor.name}?`)) deleteMut.mutate(competitor.id) }}>Delete competitor</button>
                      </div>
                    </details>
                  </div>
                  {expanded && (
                    <div className="competitor-detail" role="cell">
                      <div><span>Lifecycle</span><strong>{run ? `${stateLabel(run.operator_state)} · attempt ${run.attempt}/${run.max_attempts}` : 'No active run'}</strong><small>Queued {formatDate(run?.queued_at)} · claimed {formatDate(run?.claimed_at)} · recorded {formatDate(run?.terminal_at)}</small></div>
                      <div><span>Acquisition</span><strong>{run?.acquisition_strategy || 'Strategy not recorded'}</strong><small>{run ? `${run.products_observed.toLocaleString()} observed · ${run.pages_fetched} pages / ${run.request_count} requests · ${run.acquisition_duration_seconds?.toFixed(1) || '—'}s acquire` : 'No current acquisition evidence'}</small></div>
                      <div><span>Coverage evidence</span><strong>{coverage}</strong><small>{run?.completeness_reason || `Latest complete ${formatDate(fresh?.last_complete_at)}`}</small></div>
                      <div><span>Identifiers</span><strong>{run ? `Run #${run.run_id}` : 'No run ID'}</strong><small>{visibleRequest && run ? `Request ${visibleRequest.request_id}` : 'Open technical identifiers appear only for the active request.'}</small></div>
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        )}
      </section>

      <CompetitorForm open={modalOpen} onClose={() => setModalOpen(false)} onSubmit={data => createMut.mutate(data)} loading={createMut.isPending} />
      <CompetitorForm open={!!editing} onClose={() => setEditing(null)} initial={editing} onSubmit={data => { if (editing) updateMut.mutate({ id: editing.id, data }) }} loading={updateMut.isPending} />
    </div>
  )
}
