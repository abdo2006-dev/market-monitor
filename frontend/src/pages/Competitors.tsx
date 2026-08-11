import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getCompetitors, createCompetitor, updateCompetitor, deleteCompetitor, scanNow, scanAllCompetitors, seedDefaultCompetitors, getSyncFreshness, getSyncRequest } from '../lib/api'
import { Card, Button, Table, Tr, Td, Loading, EmptyState, ErrorState } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { timeAgo } from '../lib/utils'
import CompetitorForm from '../components/CompetitorForm'
import type { Competitor, CompetitorInput, SyncRequestStatus } from '../lib/types'

export default function CompetitorsPage() {
  const qc = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Competitor | null>(null)
  const [scanningId, setScanningId] = useState<number | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)

  const { data: competitors = [], isLoading, error } = useQuery({
    queryKey: ['competitors'], queryFn: getCompetitors,
  })
  const { data: freshness = [] } = useQuery({
    queryKey: ['sync-freshness'], queryFn: getSyncFreshness, refetchInterval: 10000,
  })
  const { data: syncRequest } = useQuery({
    queryKey: ['sync-request', requestId],
    queryFn: () => getSyncRequest(requestId as string),
    enabled: requestId !== null,
    refetchInterval: 5000,
  })

  const createMut = useMutation({
    mutationFn: createCompetitor,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); setModalOpen(false) },
  })
  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<CompetitorInput> }) => updateCompetitor(id, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); setEditing(null) },
  })
  const deleteMut = useMutation({
    mutationFn: deleteCompetitor,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }),
  })
  const seedMut = useMutation({
    mutationFn: seedDefaultCompetitors,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }),
  })
  const scanAllMut = useMutation({
    mutationFn: scanAllCompetitors,
    onSuccess: result => {
      setRequestId(result.request_id)
      qc.invalidateQueries({ queryKey: ['sync-freshness'] })
    },
  })

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

  if (isLoading) return <div style={{ padding: '2rem' }}><Loading /></div>
  if (error) return <div style={{ padding: '2rem' }}><ErrorState message="Failed to load competitors." /></div>

  const activeCompetitors = competitors.filter(competitor => competitor.active)
  const scanControlsDisabled = scanAllMut.isPending || scanningId !== null
  const visibleRequest: SyncRequestStatus | undefined = syncRequest || scanAllMut.data
  const requestHasIssues = visibleRequest?.status === 'failed' || visibleRequest?.status === 'partial'
  const freshnessByCompetitor = new Map(freshness.map(row => [row.competitor_id, row]))
  const requestRunsByCompetitor = new Map(
    (visibleRequest?.runs || []).map(run => [run.competitor_id, run])
  )

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader
        title="Competitors"
        subtitle={`${competitors.length} competitor${competitors.length !== 1 ? 's' : ''} configured`}
        action={
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Button
              variant="secondary"
              onClick={() => scanAllMut.mutate()}
              loading={scanAllMut.isPending}
              disabled={activeCompetitors.length === 0 || scanControlsDisabled}
            >
              ▶ Scan All
            </Button>
            <Button onClick={() => setModalOpen(true)}>+ Add Competitor</Button>
          </div>
        }
      />

      {visibleRequest && (
        <div style={{
          marginBottom: 16, padding: '10px 12px', borderRadius: 8,
          background: requestHasIssues ? '#ef444414' : '#6366f114',
          border: `1px solid ${requestHasIssues ? '#ef444433' : '#6366f144'}`,
          color: requestHasIssues ? '#fca5a5' : '#c7d2fe',
          fontSize: 13,
        }}>
          <strong>Sync {visibleRequest.status.replace('_', ' ')}</strong>
          {' · '}{visibleRequest.runs.length} competitor run{visibleRequest.runs.length === 1 ? '' : 's'}
          {' · '}runner dispatch {visibleRequest.dispatch_status.replace('_', ' ')}.
          {visibleRequest.dispatch_status === 'failed' && (
            <span> The request remains queued for scheduled recovery.</span>
          )}
        </div>
      )}

      <Card>
        {competitors.length === 0 ? (
          <div>
            <EmptyState icon="🏢" title="No competitors yet"
              description="Add your first competitor or load the starter Roblox stores." />
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: -36, paddingBottom: 24 }}>
              <Button onClick={() => seedMut.mutate()} loading={seedMut.isPending}>Add Starter Stores</Button>
            </div>
          </div>
        ) : (
          <Table headers={['Name', 'Status', 'Last complete', 'Latest run', 'Observed', 'Actions']}>
            {competitors.map((c: Competitor) => {
              const fresh = freshnessByCompetitor.get(c.id)
              const run = requestRunsByCompetitor.get(c.id) || fresh?.active_run
              const runStatus = run?.status === 'retry_wait' ? 'retrying' : run?.status
              const isPartial = run?.completeness === 'partial' || run?.completeness === 'suspicious_empty'
              return (
              <Tr key={c.id}>
                <Td>
                  <div style={{ fontWeight: 600, color: '#e4e4f0' }}>{c.name}</div>
                  <div style={{ fontSize: 12, color: '#8b8fa8' }}>{c.base_url}</div>
                </Td>
                <Td>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span style={{
                      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                      background: c.active ? '#22c55e' : '#6b7280',
                    }} />
                    <span style={{ fontSize: 13, color: c.active ? '#22c55e' : '#6b7280' }}>
                      {c.active ? 'Active' : 'Inactive'}
                    </span>
                    {(runStatus || c.last_scan_status) && (
                      <span style={{ fontSize: 11, background: (runStatus === 'failed' || isPartial) ? '#ef444422' : '#6366f122', color: (runStatus === 'failed' || isPartial) ? '#fca5a5' : '#a5b4fc', padding: '2px 6px', borderRadius: 4 }}>
                        {isPartial ? run?.completeness.replace('_', ' ') : (runStatus || c.last_scan_status)}
                      </span>
                    )}
                  </div>
                </Td>
                <Td style={{ color: '#8b8fa8' }}>
                  {fresh?.last_complete_at ? timeAgo(fresh.last_complete_at) : 'Never'}
                </Td>
                <Td>
                  <div style={{ fontSize: 12, color: '#c7d2fe' }}>{runStatus || (fresh?.coverage_complete ? 'complete' : 'no complete coverage')}</div>
                  {run?.failure_reason && <div style={{ fontSize: 11, color: '#fca5a5', maxWidth: 220 }}>{run.failure_reason}</div>}
                  {run?.completeness_reason && isPartial && <div style={{ fontSize: 11, color: '#fca5a5', maxWidth: 220 }}>{run.completeness_reason}</div>}
                </Td>
                <Td>
                  <div>{run ? run.products_observed.toLocaleString() : '—'}</div>
                  {run?.duration_seconds != null && <div style={{ fontSize: 11, color: '#8b8fa8' }}>{run.duration_seconds.toFixed(1)}s · attempt {run.attempt}/{run.max_attempts}</div>}
                </Td>
                <Td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => handleScanNow(c.id)}
                      loading={scanningId === c.id}
                      disabled={scanControlsDisabled || !c.active}
                    >
                      ▶ Scan
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => handleToggleActive(c)}>
                      {c.active ? 'Disable' : 'Enable'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(c)}>Edit</Button>
                    <Button size="sm" variant="danger" onClick={() => {
                      if (confirm(`Delete ${c.name}?`)) deleteMut.mutate(c.id)
                    }}>Delete</Button>
                  </div>
                </Td>
              </Tr>
            )})}
          </Table>
        )}
      </Card>

      <CompetitorForm
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={data => createMut.mutate(data)}
        loading={createMut.isPending}
      />
      <CompetitorForm
        open={!!editing}
        onClose={() => setEditing(null)}
        initial={editing}
        onSubmit={data => { if (editing) updateMut.mutate({ id: editing.id, data }) }}
        loading={updateMut.isPending}
      />
    </div>
  )
}
