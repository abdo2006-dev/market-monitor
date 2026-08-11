import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompareResponse, SearchSuggestion, SearchSuggestionsResponse } from '../lib/types'
import MarketSearchPage from './MarketSearch'
import {
  compareProduct,
  getSearchSuggestions,
  getSyncRequest,
  scanAllCompetitors,
} from '../lib/api'

vi.mock('../lib/api', () => ({
  compareProduct: vi.fn(),
  getSearchSuggestions: vi.fn(),
  getSyncRequest: vi.fn(),
  scanAllCompetitors: vi.fn(),
}))

const mockedSuggestions = vi.mocked(getSearchSuggestions)
const mockedCompare = vi.mocked(compareProduct)
const mockedSyncRequest = vi.mocked(getSyncRequest)
const mockedScanAll = vi.mocked(scanAllCompetitors)

const suggestion: SearchSuggestion = {
  title: 'Batwing',
  normalized_title: 'murder mystery 2::batwing::normal',
  base_title: 'Batwing',
  base_normalized_title: 'batwing',
  mutation: 'normal',
  mutation_label: 'Normal',
  category: 'Murder Mystery 2',
  representative_product_id: 10,
  best_price: 9,
  currency: 'USD',
  image_url: null,
  competitors: ['Alpha Store'],
  competitors_count: 1,
  variants: ['Batwing'],
  match_score: 1,
  prices_by_currency: [{ currency: 'USD', lowest_observed_price: 9 }],
}

const suggestionsResponse: SearchSuggestionsResponse = {
  items: [suggestion],
  total: 1,
  query: 'Batwing',
  candidates_considered: 1,
  candidate_limit_reached: false,
}

const product = {
  id: 10,
  competitor_id: 1,
  title: 'Batwing',
  normalized_title: 'batwing',
  category: 'Murder Mystery 2',
  url: 'https://alpha.example/products/batwing',
  image_url: null,
  current_price: 9,
  currency: 'USD',
  stock_status: 'in_stock',
  first_seen_at: '2026-08-12T05:00:00Z',
  last_seen_at: '2026-08-12T07:30:00Z',
  last_checked_at: '2026-08-12T07:31:00Z',
  last_observed_at: '2026-08-12T07:30:00Z',
  last_observed_run_id: 42,
  active: true,
}

function comparison(overrides?: {
  state?: 'current_complete' | 'partial' | 'failed' | 'stale' | 'unknown'
  reliable?: boolean
  active?: boolean
  noReliable?: boolean
}): CompareResponse {
  const state = overrides?.state || 'current_complete'
  const reliable = overrides?.reliable ?? true
  const noReliable = overrides?.noReliable ?? !reliable
  return {
    target: product,
    identity: {
      key: 'murder mystery 2::batwing::normal',
      item_key: 'batwing::normal',
      collection: 'murder mystery 2',
      collection_label: 'Murder Mystery 2',
      base: 'batwing',
      mutation: 'normal',
      mutation_label: 'Normal',
      display_title: 'Batwing',
    },
    aliases: ['batwing'],
    total_matches: 1,
    market_summary: {
      currencies: [{
        currency: 'USD',
        lowest_reliable_price: reliable ? 9 : null,
        lowest_reliable_competitor_id: reliable ? 1 : null,
        lowest_reliable_competitor_name: reliable ? 'Alpha Store' : null,
        lowest_observed_price: 9,
        lowest_observed_competitor_id: 1,
        lowest_observed_competitor_name: 'Alpha Store',
        highest_reliable_price: reliable ? 9 : null,
        median_reliable_price: reliable ? 9 : null,
        observed_price_count: 1,
        reliable_price_count: reliable ? 1 : 0,
      }],
      competitors_carrying: 1,
      trustworthy_current: reliable ? 1 : 0,
      degraded_or_unknown: reliable ? 0 : 1,
      syncing_competitors: overrides?.active ? 1 : 0,
      no_reliable_prices: noReliable,
    },
    items: [{
      competitor_id: 1,
      competitor_name: 'Alpha Store',
      match_score: 1,
      product,
      price_change: null,
      reference_currency: reliable ? 'USD' : null,
      difference_from_reliable_low: reliable ? 0 : null,
      difference_percentage: reliable ? 0 : null,
      trust: {
        coverage_state: state,
        price_reliability: reliable ? 'reliable' : state === 'unknown' ? 'unknown' : 'degraded',
        reliable,
        trustworthy_current_observation: reliable,
        product_observed_at: '2026-08-12T07:30:00Z',
        product_observation_age_seconds: 480,
        latest_complete_at: '2026-08-12T07:30:00Z',
        complete_coverage_age_seconds: 480,
        latest_partial_at: state === 'partial' ? '2026-08-12T07:35:00Z' : null,
        last_failed_at: state === 'failed' ? '2026-08-12T07:40:00Z' : null,
        required_cycle_date: '2026-08-12',
        current_completeness: state === 'partial' ? 'partial' : state === 'failed' ? 'failed' : 'complete',
        warning: reliable ? null : state === 'partial'
          ? 'Latest catalog observation was partial; this price is excluded from the reliable range.'
          : 'No trustworthy V2 catalog lineage is available for this stored price.',
        producing_run: {
          run_id: 42,
          status: 'success',
          completeness: state === 'partial' ? 'partial' : 'complete',
          observation_completed_at: '2026-08-12T07:30:00Z',
          terminal_at: '2026-08-12T07:31:00Z',
          failure_category: null,
          failure_reason: null,
        },
        latest_complete_run: {
          run_id: 42,
          status: 'success',
          completeness: 'complete',
          observation_completed_at: '2026-08-12T07:30:00Z',
          terminal_at: '2026-08-12T07:31:00Z',
          failure_category: null,
          failure_reason: null,
        },
        latest_partial_run: state === 'partial' ? {
          run_id: 44,
          status: 'success',
          completeness: 'partial',
          observation_completed_at: '2026-08-12T07:35:00Z',
          terminal_at: '2026-08-12T07:36:00Z',
          failure_category: null,
          failure_reason: null,
        } : null,
        latest_failed_run: state === 'failed' ? {
          run_id: 45,
          status: 'failed',
          completeness: 'failed',
          observation_completed_at: null,
          terminal_at: '2026-08-12T07:40:00Z',
          failure_category: 'timeout',
          failure_reason: 'External catalog request timed out',
        } : null,
        active_sync: overrides?.active ? {
          run_id: 43,
          status: 'running',
          completeness: 'unknown',
          observation_completed_at: null,
          terminal_at: null,
          failure_category: null,
          failure_reason: null,
        } : null,
      },
    }],
  }
}

function renderSearch() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/search?q=Batwing']}>
        <MarketSearchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function selectBatwing() {
  await userEvent.click(await screen.findByRole('option', { name: /Batwing/i }))
}

describe('Market Search daily-use states', () => {
  beforeEach(() => {
    mockedSuggestions.mockResolvedValue(suggestionsResponse)
    mockedCompare.mockResolvedValue(comparison())
    mockedSyncRequest.mockReset()
    mockedScanAll.mockReset()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('shows a comparison loading skeleton after a product is selected', async () => {
    mockedCompare.mockImplementation(() => new Promise(() => undefined))
    renderSearch()
    await selectBatwing()
    expect(screen.getByLabelText('Loading competitor comparison')).toBeInTheDocument()
  })

  it('renders the trustworthy market summary and successful competitor result', async () => {
    renderSearch()
    await selectBatwing()
    expect(await screen.findByText('Trustworthy price range')).toBeInTheDocument()
    expect(screen.getByText('Lowest reliable')).toBeInTheDocument()
    expect(screen.getAllByText('$9.00').length).toBeGreaterThan(0)
    expect(screen.getByText('Complete coverage')).toBeInTheDocument()
    expect(screen.getByText('Reliable low')).toBeInTheDocument()
  })

  it('keeps a partial price visible and explains degraded freshness', async () => {
    mockedCompare.mockResolvedValue(comparison({ state: 'partial', reliable: false }))
    renderSearch()
    await selectBatwing()
    expect(await screen.findByText('Partial catalog')).toBeInTheDocument()
    expect(screen.getByText(/Latest catalog observation was partial/)).toBeInTheDocument()
    expect(screen.getAllByText('$9.00').length).toBeGreaterThan(0)
  })

  it('clearly states when no reliable current price exists', async () => {
    mockedCompare.mockResolvedValue(comparison({ state: 'unknown', reliable: false, noReliable: true }))
    renderSearch()
    await selectBatwing()
    expect(await screen.findByText('No reliable current price is available.')).toBeInTheDocument()
    expect(screen.getByText('Not available')).toBeInTheDocument()
  })

  it('shows a stable no-results state for autocomplete', async () => {
    mockedSuggestions.mockResolvedValue({
      items: [], total: 0, query: 'Batwing', candidates_considered: 0,
      candidate_limit_reached: false,
    })
    renderSearch()
    expect(await screen.findByText(/No products matched/)).toBeInTheDocument()
  })

  it('offers a retry when the comparison API fails', async () => {
    mockedCompare.mockRejectedValue(new Error('Comparison unavailable'))
    renderSearch()
    await selectBatwing()
    expect(await screen.findByText('Comparison unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try comparison again' })).toBeInTheDocument()
  })

  it('shows active Sync independently from the prior complete observation', async () => {
    mockedCompare.mockResolvedValue(comparison({ active: true }))
    renderSearch()
    await selectBatwing()
    expect(await screen.findByText('Syncing now')).toBeInTheDocument()
    expect(screen.getByText('Complete coverage')).toBeInTheDocument()
  })

  it('does not label an accepted Sync request as refreshed', async () => {
    const accepted = {
      request_id: '2bd1c3bd-c69d-46db-994a-85c02bd8332b',
      trigger: 'manual_all',
      status: 'queued' as const,
      requested_at: '2026-08-12T08:00:00Z',
      dispatch_status: 'not_requested' as const,
      dispatch_error_category: null,
      runs: [],
    }
    mockedScanAll.mockResolvedValue(accepted)
    mockedSyncRequest.mockResolvedValue(accepted)
    renderSearch()
    await selectBatwing()
    await userEvent.click(await screen.findByRole('button', { name: 'Refresh market data' }))
    expect(await screen.findByText('Sync request accepted')).toBeInTheDocument()
    expect(screen.getByText(/has not refreshed yet/)).toBeInTheDocument()
    await waitFor(() => expect(mockedScanAll).toHaveBeenCalledTimes(1))
  })
})
