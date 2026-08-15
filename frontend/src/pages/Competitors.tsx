import React, { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
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
  seedDefaultCompetitors,
  updateCompetitor,
} from '../lib/api'
import { Button, EmptyState, ErrorState, Loading } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { formatDate, timeAgo } from '../lib/utils'
import CompetitorForm from '../components/CompetitorForm'
import type { Competitor, CompetitorFreshness, CompetitorInput, SyncRequestStatus, SyncRunStatus } from '../lib/types'
import './Competitors.css'

const ACTIVE_RUNS = new Set(['queued', 'running', 'retry_wait'])
const ISSUE_STATES = new Set(['failed', 'abandoned', 'partial', 'suspicious_empty', 'retry_wait'])

function latestEvidenceAt(fresh?: CompetitorFreshness) {
  const evidence = [fresh?.last_complete_at, fresh?.latest_partial_at, fresh?.last_failed_at]
    .filter((value): value is string => Boolean(value))
    .sort()
  return evidence[evidence.length - 1] || null
}

function displayRunState(run: SyncRunStatus | undefined, competitor: Competitor, fresh?: CompetitorFreshness) {
  if (run?.status === 'retry_wait') return 'retrying'
  if (run && run.completeness !== 'unknown' && run.completeness !== 'complete') return run.completeness
  if (run?.status) return run.status
  if (competitor.last_scan_status) return competitor.last_scan_status
  if (fresh?.coverage_complete) return 'complete'
  return 'unknown'
}

function stateLabel(state: string) {
  return ({ suspicious_empty: 'Suspicious empty', retry_wait: 'Retrying', stale_skipped: 'Stale skipped' } as Record<string, string>)[state]
    || state.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase())
}

function StateIndicator({ state }: { state: string }) {
  const tone = ['success', 'complete'].includes(state) ? 'good'
    : ['queued', 'running'].includes(state) ? 'info'
      : ['partial', 'suspicious_empty', 'retrying'].includes(state) ? 'warn'
        : ['failed', 'abandoned'].includes(state) ? 'danger' : 'muted'
  const Icon = tone === 'good' ? CheckCircle2 : tone === 'danger' ? AlertTriangle : tone === 'info' ? RefreshCw : Clock3
  return <span className={`sync-state sync-state--${tone}`}><Icon size={13} className={state === 'running' ? 'spin' : ''} />{stateLabel(state)}</span>
}

export default function CompetitorsPage() {
  const qc = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Competitor | null>(null)
  const [scanningId, setScanningId] = useState<number | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)

  const { data: competitors = [], isLoading, error } = useQuery({ queryKey: ['competitors'], queryFn: getCompetitors })
  const { data: freshness = [] } = useQuery({ queryKey: ['sync-freshness'], queryFn: getSyncFreshness, refetchInterval: 10000 })
  const { data: syncRequest } = useQuery({
    queryKey: ['sync-request', requestId],
    queryFn: () => getSyncRequest(requestId as string),
    enabled: requestId !== null,
    refetchInterval: 5000,
  })

  const createMut = useMutation({ mutationFn: createCompetitor, onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); setModalOpen(false) } })
  const updateMut = useMutation({ mutationFn: ({ id, data }: { id: number; data: Partial<CompetitorInput> }) => updateCompetitor(id, data), onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); setEditing(null) } })
  const deleteMut = useMutation({ mutationFn: deleteCompetitor, onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }) })
  const seedMut = useMutation({ mutationFn: seedDefaultCompetitors, onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }) })
  const scanAllMut = useMutation({ mutationFn: scanAllCompetitors, onSuccess: result => { setRequestId(result.request_id); qc.invalidateQueries({ queryKey: ['sync-freshness'] }) } })

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
  const states = activeCompetitors.map(competitor => {
    const fresh = freshnessByCompetitor.get(competitor.id)
    const run = requestRunsByCompetitor.get(competitor.id) || fresh?.active_run || undefined
    return { competitor, fresh, run, state: displayRunState(run, competitor, fresh) }
  })
  const activeNow = states.filter(item => item.run && ACTIVE_RUNS.has(item.run.status)).length
  const needsAttention = states.filter(item => ISSUE_STATES.has(item.state)).length
  const healthy = states.filter(item => item.fresh?.coverage_complete && !ISSUE_STATES.has(item.state)).length
  const partial = states.filter(item => ['partial', 'suspicious_empty'].includes(item.state)).length
  const completeTimes = freshness.map(item => item.last_complete_at).filter((value): value is string => Boolean(value)).sort()
  const lastComplete = completeTimes[completeTimes.length - 1]
  const requestHasIssues = visibleRequest?.status === 'failed' || visibleRequest?.status === 'partial'

  return (
    <div className="competitors-page">
      <PageHeader
        eyebrow="Market readiness"
        title="Competitor sync"
        subtitle="See whether the market is ready, what is active, and where catalog evidence needs attention."
        action={<>
          <Button variant="secondary" onClick={() => setModalOpen(true)}><Plus size={15} /> Add competitor</Button>
          <Button onClick={() => scanAllMut.mutate()} loading={scanAllMut.isPending} disabled={activeCompetitors.length === 0 || scanControlsDisabled}><RefreshCw size={15} /> Sync all</Button>
        </>}
      />

      {previewDemoMode && (
        <div className="competitors-preview-notice" role="status">
          <ShieldCheck size={16} aria-hidden="true" />
          <span><strong>Protected preview.</strong> Lifecycle examples are deterministic. New requests write only to the isolated database and remain queued without a runner.</span>
        </div>
      )}

      <section className="readiness-summary" aria-label="Market sync readiness">
        <div className="readiness-lead">
          <span className="readiness-icon"><Database size={20} /></span>
          <div><span>Most recent complete source</span><strong>{lastComplete ? timeAgo(lastComplete) : 'No complete source run'}</strong><small>{lastComplete ? formatDate(lastComplete) : 'Complete source coverage has not been established.'}</small></div>
        </div>
        <div className="readiness-metrics">
          <div><span className="metric-dot is-good" /><strong>{healthy}</strong><small>Healthy</small></div>
          <div><span className="metric-dot is-warn" /><strong>{partial}</strong><small>Partial</small></div>
          <div><span className="metric-dot is-danger" /><strong>{needsAttention}</strong><small>Attention</small></div>
          <div><span className="metric-dot is-info" /><strong>{activeNow}</strong><small>Active now</small></div>
        </div>
      </section>

      {visibleRequest && (
        <div className={`request-banner ${requestHasIssues ? 'is-warning' : ''}`} role="status">
          <span className="request-banner-icon"><RefreshCw size={16} className={['running', 'retrying'].includes(visibleRequest.status) ? 'spin' : ''} /></span>
          <div><strong>Sync request {stateLabel(visibleRequest.status).toLowerCase()}</strong><span>{visibleRequest.runs.length} run{visibleRequest.runs.length === 1 ? '' : 's'} · dispatch {visibleRequest.dispatch_status.replaceAll('_', ' ')}</span></div>
          {visibleRequest.dispatch_status === 'failed' && <small>The durable request remains queued for recovery.</small>}
        </div>
      )}

      <section className="competitor-operations">
        <div className="operations-heading"><div><span>Competitor operations</span><h2>{activeCompetitors.length} active sources</h2></div><small>Queued does not mean complete. Product counts describe only the visible run.</small></div>
        {competitors.length === 0 ? (
          <div className="competitors-empty">
            <EmptyState title="No competitors configured" description="Add a competitor to begin building trustworthy market coverage." />
            <Button onClick={() => seedMut.mutate()} loading={seedMut.isPending}>Add starter stores</Button>
          </div>
        ) : (
          <div className="competitor-rows">
            {competitors.map(competitor => {
              const fresh = freshnessByCompetitor.get(competitor.id)
              const run = requestRunsByCompetitor.get(competitor.id) || fresh?.active_run || undefined
              const state = displayRunState(run, competitor, fresh)
              const evidenceAt = latestEvidenceAt(fresh)
              const issue = ISSUE_STATES.has(state)
              const explanation = run?.failure_reason || run?.completeness_reason || (
                state === 'failed' ? 'Latest Sync failed; stored coverage remains unchanged.'
                  : state === 'suspicious_empty' ? 'Zero-product result was not trusted as complete.'
                    : state === 'partial' ? 'Catalog coverage is partial; absence inference is disabled.' : null
              )
              return (
                <article className={`competitor-row ${!competitor.active ? 'is-disabled' : ''}`} key={competitor.id}>
                  <div className="competitor-identity">
                    <span className="competitor-monogram">{competitor.name.trim().charAt(0).toUpperCase()}</span>
                    <div><strong>{competitor.name}</strong><a href={competitor.base_url} target="_blank" rel="noreferrer">{competitor.base_url.replace(/^https?:\/\//, '')}</a></div>
                  </div>
                  <div className="competitor-lifecycle">
                    <StateIndicator state={state} />
                    <div className="lifecycle-track" aria-label={`Lifecycle ${state}`}>
                      <span className={state !== 'unknown' ? 'is-complete' : ''}>Requested</span>
                      <i />
                      <span className={['running', 'retrying', 'success', 'complete', 'partial', 'failed', 'suspicious_empty'].includes(state) ? 'is-complete' : ''}>Processing</span>
                      <i />
                      <span className={['success', 'complete', 'partial', 'failed', 'suspicious_empty'].includes(state) ? (issue ? 'is-warning' : 'is-complete') : ''}>Recorded</span>
                    </div>
                  </div>
                  <div className="competitor-evidence"><span>Last complete</span><strong>{fresh?.last_complete_at ? timeAgo(fresh.last_complete_at) : 'Never'}</strong><small>{evidenceAt ? `Latest evidence ${timeAgo(evidenceAt)}` : 'No V2 evidence'}</small></div>
                  <div className="competitor-observed"><span>Observed in run</span><strong>{run ? run.products_observed.toLocaleString() : '—'}</strong><small>{run?.duration_seconds != null ? `${run.duration_seconds.toFixed(1)}s · attempt ${run.attempt}/${run.max_attempts}` : stateLabel(run?.completeness || 'unknown')}</small></div>
                  <div className="competitor-row-actions">
                    <Button size="sm" variant="secondary" onClick={() => handleScanNow(competitor.id)} loading={scanningId === competitor.id} disabled={scanControlsDisabled || !competitor.active}><Play size={13} /> Sync</Button>
                    <details className="competitor-actions-menu">
                      <summary aria-label={`More actions for ${competitor.name}`}><MoreHorizontal size={17} /></summary>
                      <div>
                        <button type="button" onClick={() => handleToggleActive(competitor)}>{competitor.active ? 'Disable source' : 'Enable source'}</button>
                        <button type="button" onClick={() => setEditing(competitor)}>Edit configuration</button>
                        <button type="button" className="is-danger" onClick={() => { if (confirm(`Delete ${competitor.name}?`)) deleteMut.mutate(competitor.id) }}>Delete competitor</button>
                      </div>
                    </details>
                  </div>
                  {explanation && <div className={`competitor-explanation ${issue ? 'is-warning' : ''}`}><AlertTriangle size={13} />{explanation}</div>}
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
