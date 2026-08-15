import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Competitor, CompetitorFreshness, SyncRequestStatus } from '../lib/types'
import CompetitorsPage from './Competitors'
import {
  getCompetitors,
  getSyncFreshness,
  getSyncRequest,
  scanAllCompetitors,
} from '../lib/api'

vi.mock('../lib/api', () => ({
  createCompetitor: vi.fn(),
  deleteCompetitor: vi.fn(),
  getCompetitors: vi.fn(),
  getSyncFreshness: vi.fn(),
  getSyncRequest: vi.fn(),
  scanAllCompetitors: vi.fn(),
  scanNow: vi.fn(),
  seedDefaultCompetitors: vi.fn(),
  updateCompetitor: vi.fn(),
}))

const mockedCompetitors = vi.mocked(getCompetitors)
const mockedFreshness = vi.mocked(getSyncFreshness)
const mockedRequest = vi.mocked(getSyncRequest)
const mockedScanAll = vi.mocked(scanAllCompetitors)

const competitors: Competitor[] = [
  {
    id: 1, name: 'Alpha Market', base_url: 'https://alpha.example', active: true,
    scan_frequency_minutes: 60, scrape_type: 'shopify_json', listing_urls: [], selector_config: {},
    last_scan_status: 'success', created_at: '2026-08-15T08:00:00Z', updated_at: '2026-08-15T08:00:00Z',
  },
  {
    id: 2, name: 'Beta Market', base_url: 'https://beta.example', active: true,
    scan_frequency_minutes: 60, scrape_type: 'shopify_json', listing_urls: [], selector_config: {},
    last_scan_status: 'partial', created_at: '2026-08-15T08:00:00Z', updated_at: '2026-08-15T08:00:00Z',
  },
]

const freshness: CompetitorFreshness[] = [
  { competitor_id: 1, competitor_name: 'Alpha Market', coverage_complete: true, last_complete_at: '2026-08-15T08:30:00Z', latest_partial_at: null, last_failed_at: null, active_run: null },
  { competitor_id: 2, competitor_name: 'Beta Market', coverage_complete: false, last_complete_at: '2026-08-14T08:30:00Z', latest_partial_at: '2026-08-15T08:35:00Z', last_failed_at: null, active_run: null },
]

const queuedRequest: SyncRequestStatus = {
  request_id: '00000000-0000-0000-0000-000000000001', trigger: 'manual_all', status: 'queued',
  requested_at: '2026-08-15T09:00:00Z', dispatch_status: 'not_requested', runs: [],
}

function renderCompetitors() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><CompetitorsPage /></QueryClientProvider>)
}

describe('Competitor sync operations center', () => {
  beforeEach(() => {
    mockedCompetitors.mockResolvedValue(competitors)
    mockedFreshness.mockResolvedValue(freshness)
    mockedScanAll.mockResolvedValue(queuedRequest)
    mockedRequest.mockResolvedValue(queuedRequest)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('summarizes readiness and keeps partial evidence visible', async () => {
    renderCompetitors()
    expect(await screen.findByRole('heading', { name: 'Competitor sync' })).toBeInTheDocument()
    expect(screen.getByText('Alpha Market')).toBeInTheDocument()
    expect(screen.getByText('Beta Market')).toBeInTheDocument()
    expect(screen.getAllByText('Partial').length).toBeGreaterThan(0)
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    expect(screen.getByText('Attention')).toBeInTheDocument()
  })

  it('reports Sync All as a durable request rather than a completed refresh', async () => {
    renderCompetitors()
    await userEvent.click(await screen.findByRole('button', { name: /Sync all/i }))
    expect(await screen.findByText('Sync request queued')).toBeInTheDocument()
    expect(screen.getByText(/dispatch not requested/i)).toBeInTheDocument()
  })
})
