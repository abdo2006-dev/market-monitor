import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CollectionExportDownload, CollectionExportFailure, Competitor } from '../lib/types'
import ExportsPage from './Exports'
import { getCompetitors, prepareCollectionExport } from '../lib/api'

vi.mock('../lib/api', () => ({
  getCompetitors: vi.fn(),
  prepareCollectionExport: vi.fn(),
}))

const mockedCompetitors = vi.mocked(getCompetitors)
const mockedPrepare = vi.mocked(prepareCollectionExport)

const competitor: Competitor = {
  id: 1, name: 'Alpha Store', base_url: 'https://alpha.example', category: null,
  active: true, scrape_type: 'shopify_json', listing_urls: [], selector_config: {},
  scan_frequency_minutes: 60, last_scan_at: null, last_scan_status: null,
  created_at: '2026-08-12T07:00:00Z', updated_at: '2026-08-12T07:00:00Z',
}

function download(overrides: Partial<CollectionExportDownload['provenance']> = {}): CollectionExportDownload {
  return {
    blob: new Blob(['title,price\nBatwing,9\n'], { type: 'text/csv' }),
    filename: 'alpha-store-murder-mystery-2-prices.csv',
    provenance: {
      requested_mode: 'live', source: 'live', completeness: 'complete', products_count: 1,
      pages_fetched: 1, page_cap_reached: false,
      acquisition_started_at: '2026-08-12T07:29:00Z', acquisition_completed_at: '2026-08-12T07:30:00Z',
      observation_started_at: '2026-08-12T07:30:00Z', observation_completed_at: '2026-08-12T07:30:00Z',
      safe_reason: 'Adapter reached a catalog end signal', cached_coverage_basis: null,
      coverage_state: 'current_complete', newest_observed_at: null, oldest_observed_at: null,
      latest_complete_run_id: null, latest_complete_at: null, latest_terminal_run_id: null,
      degraded_or_legacy_row_count: 0, ...overrides,
    },
  }
}

function renderExports() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><ExportsPage /></QueryClientProvider>)
}

async function fillRequiredFields() {
  await userEvent.selectOptions(await screen.findByLabelText('Competitor'), '1')
  await userEvent.type(screen.getByLabelText('Collection URL'), 'https://alpha.example/collections/murder-mystery-2')
}

function typedFailure(code: CollectionExportFailure['code'], message: string): Error {
  const error = new Error(message) as Error & { isAxiosError: boolean; response: { data: unknown } }
  error.isAxiosError = true
  error.response = { data: { detail: { code, message, provenance: download().provenance } } }
  return error
}

describe('Collection exports daily-use states', () => {
  beforeEach(() => {
    mockedCompetitors.mockResolvedValue([competitor])
    mockedPrepare.mockResolvedValue(download())
    class TestURL extends URL {}
    Object.defineProperty(TestURL, 'createObjectURL', { value: vi.fn(() => 'blob:prepared') })
    Object.defineProperty(TestURL, 'revokeObjectURL', { value: vi.fn() })
    vi.stubGlobal('URL', TestURL)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('defaults to the live mode and explains that it will not substitute stored data', async () => {
    renderExports()
    expect(await screen.findByRole('radio', { name: /Live current collection/i })).toBeChecked()
    expect(screen.getByText(/never becomes stored data/i)).toBeInTheDocument()
  })

  it('prepares a complete live export before offering the browser download', async () => {
    renderExports()
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare export' }))
    expect(await screen.findByText('Complete catalog')).toBeInTheDocument()
    expect(screen.getByText('1 products ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Download JSONL export/i })).toBeInTheDocument()
    expect(mockedPrepare).toHaveBeenCalledWith(expect.objectContaining({ mode: 'live' }))
  })

  it('presents partial coverage and a page-cap warning without hiding the file', async () => {
    mockedPrepare.mockResolvedValue(download({ completeness: 'partial', page_cap_reached: true, products_count: 1250, safe_reason: 'Pagination cap reached while another page may exist' }))
    renderExports()
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare export' }))
    expect(await screen.findByText('Partial live data')).toBeInTheDocument()
    expect(screen.getByText(/Page limit reached/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Download partial JSONL export/i })).toBeInTheDocument()
  })

  it('shows honest loading while synchronous acquisition is in progress', async () => {
    mockedPrepare.mockImplementation(() => new Promise(() => undefined))
    renderExports()
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare export' }))
    expect(screen.getByText(/Acquiring and serializing/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Acquiring collection/i })).toBeDisabled()
  })

  it('shows a structured live failure and offers explicit cached preparation', async () => {
    mockedPrepare.mockRejectedValue(typedFailure('live_acquisition_failed', 'The live collection could not be acquired. Retry it or choose stored data explicitly.'))
    renderExports()
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare export' }))
    expect(await screen.findByText('Live acquisition failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prepare latest stored data' })).toBeInTheDocument()
  })

  it('uses cached data only after the operator explicitly selects it', async () => {
    mockedPrepare.mockResolvedValue(download({ requested_mode: 'cached', source: 'cached', completeness: 'unknown', coverage_state: 'unknown', newest_observed_at: '2026-08-10T07:30:00Z', oldest_observed_at: '2026-08-08T07:30:00Z', degraded_or_legacy_row_count: 1 }))
    renderExports()
    await userEvent.click(await screen.findByRole('radio', { name: /Latest stored data/i }))
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare export' }))
    expect(await screen.findByText('STORED')).toBeInTheDocument()
    expect(mockedPrepare).toHaveBeenCalledWith(expect.objectContaining({ mode: 'cached' }))
  })

  it('renders an explicit no-stored-data error', async () => {
    mockedPrepare.mockRejectedValue(typedFailure('cached_export_unavailable', 'No active stored products match this collection. Run a live export first.'))
    renderExports()
    await userEvent.click(await screen.findByRole('radio', { name: /Latest stored data/i }))
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare export' }))
    expect(await screen.findByText(/No active stored products match/i)).toBeInTheDocument()
  })

  it('downloads only the prepared bytes when the operator confirms', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    renderExports()
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare export' }))
    await userEvent.click(await screen.findByRole('button', { name: /Download JSONL export/i }))
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    click.mockRestore()
  })
})
