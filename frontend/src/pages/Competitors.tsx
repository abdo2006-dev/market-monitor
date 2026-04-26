import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getCompetitors, createCompetitor, updateCompetitor, deleteCompetitor, scanNow } from '../lib/api'
import { Card, Button, Table, Tr, Td, Loading, EmptyState, ErrorState } from '../components/ui'
import { PageHeader } from '../components/layout/Sidebar'
import { timeAgo } from '../lib/utils'
import CompetitorForm from '../components/CompetitorForm'

export default function CompetitorsPage() {
  const qc = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<any>(null)
  const [scanningId, setScanningId] = useState<number | null>(null)

  const { data: competitors = [], isLoading, error } = useQuery({
    queryKey: ['competitors'], queryFn: getCompetitors,
  })

  const createMut = useMutation({
    mutationFn: createCompetitor,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); setModalOpen(false) },
  })
  const updateMut = useMutation({
    mutationFn: ({ id, data }: any) => updateCompetitor(id, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['competitors'] }); setEditing(null) },
  })
  const deleteMut = useMutation({
    mutationFn: deleteCompetitor,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }),
  })

  const handleScanNow = async (id: number) => {
    setScanningId(id)
    try { await scanNow(id) } finally { setScanningId(null) }
  }

  const handleToggleActive = (competitor: any) => {
    updateMut.mutate({ id: competitor.id, data: { active: !competitor.active } })
  }

  if (isLoading) return <div style={{ padding: '2rem' }}><Loading /></div>
  if (error) return <div style={{ padding: '2rem' }}><ErrorState message="Failed to load competitors." /></div>

  return (
    <div style={{ padding: '2rem' }}>
      <PageHeader
        title="Competitors"
        subtitle={`${competitors.length} competitor${competitors.length !== 1 ? 's' : ''} configured`}
        action={<Button onClick={() => setModalOpen(true)}>+ Add Competitor</Button>}
      />

      <Card>
        {competitors.length === 0 ? (
          <EmptyState icon="🏢" title="No competitors yet"
            description="Add your first competitor to start monitoring prices." />
        ) : (
          <Table headers={['Name', 'Category', 'Status', 'Frequency', 'Last Scan', 'Products', 'Actions']}>
            {competitors.map((c: any) => (
              <Tr key={c.id}>
                <Td>
                  <div style={{ fontWeight: 600, color: '#e4e4f0' }}>{c.name}</div>
                  <div style={{ fontSize: 12, color: '#8b8fa8' }}>{c.base_url}</div>
                </Td>
                <Td>{c.category || '—'}</Td>
                <Td>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span style={{
                      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                      background: c.active ? '#22c55e' : '#6b7280',
                    }} />
                    <span style={{ fontSize: 13, color: c.active ? '#22c55e' : '#6b7280' }}>
                      {c.active ? 'Active' : 'Inactive'}
                    </span>
                    {c.last_scan_status === 'failed' && (
                      <span style={{ fontSize: 11, background: '#ef444422', color: '#ef4444', padding: '2px 6px', borderRadius: 4 }}>failed</span>
                    )}
                  </div>
                </Td>
                <Td>{c.scan_frequency_minutes}m</Td>
                <Td style={{ color: '#8b8fa8' }}>
                  {c.last_scan_at ? timeAgo(c.last_scan_at) : 'Never'}
                </Td>
                <Td>
                  <span style={{ fontSize: 12, color: '#8b8fa8' }}>
                    {(c.listing_urls || []).length} URLs
                  </span>
                </Td>
                <Td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <Button size="sm" variant="secondary" onClick={() => handleScanNow(c.id)} loading={scanningId === c.id}>
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
            ))}
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
        onSubmit={data => updateMut.mutate({ id: editing.id, data })}
        loading={updateMut.isPending}
      />
    </div>
  )
}
