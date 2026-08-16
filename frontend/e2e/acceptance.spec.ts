import { expect, test, type Page, type Route } from '@playwright/test'
import { readFile } from 'node:fs/promises'

type MockOptions = {
  compareFailure?: boolean
  noReliable?: boolean
  exportState?: 'complete' | 'partial' | 'suspicious_empty' | 'failure'
  authRequired?: boolean
}

const competitor = {
  id: 1,
  name: 'Alpha Market',
  base_url: 'https://alpha.example',
  category: 'Roblox marketplace',
  active: true,
  scan_frequency_minutes: 60,
  scrape_type: 'shopify_json',
  listing_urls: [],
  selector_config: {},
  last_scan_at: '2026-08-16T07:30:00Z',
  last_scan_status: 'success',
  created_at: '2026-08-01T07:00:00Z',
  updated_at: '2026-08-16T07:31:00Z',
}

const product = {
  id: 10,
  competitor_id: 1,
  title: 'Batwing',
  normalized_title: 'batwing',
  category: 'Murder Mystery 2',
  url: 'https://alpha.example/products/batwing',
  image_url: null,
  current_price: 45,
  currency: 'USD',
  stock_status: 'in_stock',
  first_seen_at: '2026-08-10T07:00:00Z',
  last_seen_at: '2026-08-16T07:30:00Z',
  last_checked_at: '2026-08-16T07:31:00Z',
  last_observed_at: '2026-08-16T07:30:00Z',
  last_observed_run_id: 42,
  active: true,
}

const run = (overrides: Record<string, unknown> = {}) => ({
  run_id: 42,
  competitor_id: 1,
  competitor_name: 'Alpha Market',
  status: 'success',
  trigger: 'manual',
  queued_at: '2026-08-16T07:29:00Z',
  claimed_at: '2026-08-16T07:29:01Z',
  acquisition_started_at: '2026-08-16T07:29:02Z',
  acquisition_completed_at: '2026-08-16T07:30:00Z',
  observation_started_at: '2026-08-16T07:29:02Z',
  observation_completed_at: '2026-08-16T07:30:00Z',
  reconciliation_started_at: '2026-08-16T07:30:01Z',
  terminal_at: '2026-08-16T07:30:02Z',
  next_attempt_at: null,
  lease_expires_at: null,
  attempt: 1,
  max_attempts: 3,
  failure_category: null,
  failure_reason: null,
  completeness: 'complete',
  completeness_reason: 'Catalog end proved',
  products_observed: 1,
  pages_fetched: 2,
  request_count: 2,
  page_cap_reached: false,
  acquisition_strategy: 'shopify_products_json_aiohttp',
  duration_seconds: 60,
  ...overrides,
})

function suggestionPayload(query = 'Batwing') {
  if (query.toLowerCase().includes('missing')) {
    return { items: [], total: 0, query, candidates_considered: 0, candidate_limit_reached: false }
  }
  return {
    items: [{
      title: 'Batwing',
      normalized_title: 'murder mystery 2::batwing::normal',
      base_title: 'Batwing',
      base_normalized_title: 'batwing',
      mutation: 'normal',
      mutation_label: 'Normal',
      category: 'Murder Mystery 2',
      representative_product_id: 10,
      best_price: 41,
      currency: 'MULTI',
      image_url: null,
      competitors: ['Alpha Market', 'Beta Market'],
      competitors_count: 2,
      variants: ['Batwing'],
      match_score: 1,
      prices_by_currency: [
        { currency: 'USD', lowest_observed_price: 45 },
        { currency: 'EUR', lowest_observed_price: 41 },
      ],
    }],
    total: 1,
    query,
    candidates_considered: 2,
    candidate_limit_reached: false,
  }
}

function comparePayload(noReliable = false) {
  const reliable = !noReliable
  const reliableTrust = {
    coverage_state: noReliable ? 'unknown' : 'current_complete',
    price_reliability: reliable ? 'reliable' : 'unknown',
    reliable,
    trustworthy_current_observation: reliable,
    product_observed_at: '2026-08-16T07:30:00Z',
    product_observation_age_seconds: 300,
    latest_complete_at: reliable ? '2026-08-16T07:30:00Z' : null,
    complete_coverage_age_seconds: reliable ? 300 : null,
    latest_partial_at: null,
    last_failed_at: null,
    required_cycle_date: '2026-08-16',
    current_completeness: reliable ? 'complete' : 'unknown',
    warning: reliable ? null : 'No trustworthy V2 catalog lineage is available for this stored price.',
    producing_run: reliable ? run() : null,
    latest_complete_run: reliable ? run() : null,
    latest_partial_run: null,
    latest_failed_run: null,
    active_sync: null,
  }
  const degradedProduct = {
    ...product,
    id: 11,
    competitor_id: 2,
    url: 'https://beta.example/products/batwing',
    current_price: 41,
    currency: 'EUR',
    stock_status: 'out_of_stock',
    last_observed_run_id: 43,
  }
  const degradedTrust = {
    ...reliableTrust,
    coverage_state: 'partial',
    price_reliability: 'degraded',
    reliable: false,
    trustworthy_current_observation: false,
    latest_partial_at: '2026-08-16T07:35:00Z',
    current_completeness: 'partial',
    warning: 'Latest catalog observation was partial; this price is excluded from the reliable range.',
    producing_run: run({ run_id: 43, competitor_id: 2, competitor_name: 'Beta Market', completeness: 'partial' }),
    latest_complete_run: null,
    latest_partial_run: run({ run_id: 43, competitor_id: 2, competitor_name: 'Beta Market', completeness: 'partial' }),
  }
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
    total_matches: 2,
    market_summary: {
      currencies: [
        {
          currency: 'USD',
          lowest_reliable_price: reliable ? 45 : null,
          lowest_reliable_competitor_id: reliable ? 1 : null,
          lowest_reliable_competitor_name: reliable ? 'Alpha Market' : null,
          lowest_observed_price: 45,
          lowest_observed_competitor_id: 1,
          lowest_observed_competitor_name: 'Alpha Market',
          highest_reliable_price: reliable ? 45 : null,
          median_reliable_price: reliable ? 45 : null,
          observed_price_count: 1,
          reliable_price_count: reliable ? 1 : 0,
        },
        {
          currency: 'EUR',
          lowest_reliable_price: null,
          lowest_reliable_competitor_id: null,
          lowest_reliable_competitor_name: null,
          lowest_observed_price: 41,
          lowest_observed_competitor_id: 2,
          lowest_observed_competitor_name: 'Beta Market',
          highest_reliable_price: null,
          median_reliable_price: null,
          observed_price_count: 1,
          reliable_price_count: 0,
        },
      ],
      competitors_carrying: 2,
      trustworthy_current: reliable ? 1 : 0,
      degraded_or_unknown: reliable ? 1 : 2,
      syncing_competitors: 0,
      no_reliable_prices: !reliable,
    },
    items: [
      {
        competitor_id: 1,
        competitor_name: 'Alpha Market',
        match_score: 1,
        product,
        price_change: { previous_price: 60, current_price: 45, amount: 15, percentage: 25, currency: 'USD', changed_at: '2026-08-16T07:30:00Z', direction: 'decrease' },
        reference_currency: reliable ? 'USD' : null,
        difference_from_reliable_low: reliable ? 0 : null,
        difference_percentage: reliable ? 0 : null,
        trust: reliableTrust,
      },
      {
        competitor_id: 2,
        competitor_name: 'Beta Market',
        match_score: 1,
        product: degradedProduct,
        price_change: null,
        reference_currency: null,
        difference_from_reliable_low: null,
        difference_percentage: null,
        trust: degradedTrust,
      },
    ],
  }
}

function exportHeaders(state: NonNullable<MockOptions['exportState']>, mode: string, format: string) {
  const partial = state === 'partial'
  const suspicious = state === 'suspicious_empty'
  const source = mode === 'cached' ? 'cached' : 'live'
  const count = suspicious ? 0 : 2
  const extension = format === 'jsonl' ? 'jsonl' : format
  return {
    'content-type': format === 'csv' ? 'text/csv; charset=utf-8' : format === 'json' ? 'application/json; charset=utf-8' : 'application/x-ndjson; charset=utf-8',
    'content-disposition': `attachment; filename="alpha-market-murder-mystery-2-prices.${extension}"`,
    'access-control-expose-headers': [
      'Content-Disposition',
      'X-Market-Monitor-Export-Requested-Mode',
      'X-Market-Monitor-Export-Source',
      'X-Market-Monitor-Export-Completeness',
      'X-Market-Monitor-Export-Products-Count',
      'X-Market-Monitor-Export-Pages-Fetched',
      'X-Market-Monitor-Export-Page-Cap-Reached',
      'X-Market-Monitor-Export-Safe-Reason',
      'X-Market-Monitor-Export-Coverage-State',
      'X-Market-Monitor-Export-Observation-Completed-At',
      'X-Market-Monitor-Export-Oldest-Observed-At',
      'X-Market-Monitor-Export-Newest-Observed-At',
      'X-Market-Monitor-Export-Degraded-Or-Legacy-Row-Count',
    ].join(', '),
    'x-market-monitor-export-requested-mode': mode,
    'x-market-monitor-export-source': source,
    'x-market-monitor-export-completeness': source === 'cached' ? 'unknown' : suspicious ? 'suspicious_empty' : partial ? 'partial' : 'complete',
    'x-market-monitor-export-products-count': String(count),
    'x-market-monitor-export-pages-fetched': suspicious ? '1' : '2',
    'x-market-monitor-export-page-cap-reached': String(partial),
    'x-market-monitor-export-safe-reason': suspicious ? 'Unexpected zero-product result; absence inference disabled' : partial ? 'Pagination cap reached while another page may exist' : source === 'cached' ? 'Stored rows may span observations' : 'Adapter reached a catalog end signal',
    'x-market-monitor-export-coverage-state': source === 'cached' ? 'unknown' : suspicious ? 'suspicious_empty' : partial ? 'partial' : 'current_complete',
    'x-market-monitor-export-observation-completed-at': '2026-08-16T07:30:00Z',
    'x-market-monitor-export-oldest-observed-at': source === 'cached' ? '2026-08-14T07:30:00Z' : '',
    'x-market-monitor-export-newest-observed-at': source === 'cached' ? '2026-08-16T07:30:00Z' : '',
    'x-market-monitor-export-degraded-or-legacy-row-count': source === 'cached' ? '1' : '0',
  }
}

function exportBody(format: string) {
  const rows = [
    { title: 'Bátwing', price: 45.125, currency: 'USD' },
    { title: 'Chroma Luger', price: 50.5, currency: 'USD' },
  ]
  if (format === 'csv') return 'title,price,currency\nBátwing,45.125,USD\nChroma Luger,50.5,USD\n'
  if (format === 'json') return JSON.stringify({ competitor: 'Alpha Market', collection_url: 'https://alpha.example/collections/murder-mystery-2', products_count: 2, items: rows })
  return `${rows.map(row => JSON.stringify(row)).join('\n')}\n`
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installApi(page: Page, options: MockOptions = {}) {
  let authenticated = !options.authRequired
  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === '/api/auth/status') return fulfillJson(route, { enabled: Boolean(options.authRequired), authenticated })
    if (path === '/api/auth/login' && request.method() === 'POST') {
      if (request.postDataJSON()?.password !== 'preview-secret') {
        return fulfillJson(route, { detail: 'Invalid credentials' }, 401)
      }
      authenticated = true
      return fulfillJson(route, { enabled: true, authenticated: true })
    }
    if (options.authRequired && !authenticated) return fulfillJson(route, { detail: 'Authentication required' }, 401)
    if (path === '/api/competitors') return fulfillJson(route, [competitor, { ...competitor, id: 2, name: 'Beta Market', base_url: 'https://beta.example', last_scan_status: 'partial' }])
    if (path === '/api/sync/freshness') return fulfillJson(route, [
      { competitor_id: 1, competitor_name: 'Alpha Market', coverage_complete: true, last_complete_at: '2026-08-16T07:30:00Z', latest_partial_at: null, last_failed_at: null, active_run: null },
      { competitor_id: 2, competitor_name: 'Beta Market', coverage_complete: false, last_complete_at: '2026-08-15T07:30:00Z', latest_partial_at: '2026-08-16T07:35:00Z', last_failed_at: null, active_run: null },
    ])
    if (path === '/api/search/suggestions') return fulfillJson(route, suggestionPayload(url.searchParams.get('q') || ''))
    if (path === '/api/search/compare') {
      if (options.compareFailure) return fulfillJson(route, { detail: 'Comparison unavailable' }, 503)
      return fulfillJson(route, comparePayload(options.noReliable))
    }
    if (path === '/api/sync/all' && request.method() === 'POST') return fulfillJson(route, {
      request_id: '00000000-0000-0000-0000-000000000001', trigger: 'manual_all', status: 'queued',
      requested_at: '2026-08-16T08:00:00Z', dispatch_status: 'not_requested', dispatch_error_category: null, runs: [],
    }, 202)
    if (path.startsWith('/api/sync/requests/')) return fulfillJson(route, {
      request_id: '00000000-0000-0000-0000-000000000001', trigger: 'manual_all', status: 'queued',
      requested_at: '2026-08-16T08:00:00Z', dispatch_status: 'not_requested', dispatch_error_category: null, runs: [],
    })
    if (path === '/api/exports/collection-prices') {
      const state = options.exportState || 'complete'
      const format = url.searchParams.get('format') || 'jsonl'
      const mode = url.searchParams.get('mode') || 'live'
      if (state === 'failure' && mode === 'live') return fulfillJson(route, {
        detail: {
          code: 'live_acquisition_failed',
          message: 'The live collection could not be acquired. Retry it or choose stored data explicitly.',
          provenance: { requested_mode: 'live', source: 'live', completeness: 'failed', products_count: 0, pages_fetched: 0, page_cap_reached: false, safe_reason: 'temporary network' },
        },
      }, 502)
      await route.fulfill({
        status: 200,
        headers: exportHeaders(state, mode, format),
        body: state === 'suspicious_empty' ? (format === 'json' ? JSON.stringify({ competitor: 'Alpha Market', products_count: 0, items: [] }) : '') : exportBody(format),
      })
      return
    }
    return fulfillJson(route, { detail: `Unhandled deterministic fixture route: ${path}` }, 404)
  })
}

test('Production login gate blocks workspace data until authentication', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-1440', 'One browser covers the shared authentication gate')
  const consoleErrors = watchConsole(page)
  await installApi(page, { authRequired: true })
  await page.goto('/search')
  await expect(page.getByRole('heading', { name: 'Market Monitor' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Search products' })).toHaveCount(0)
  const password = page.getByLabel('Workspace password')
  await password.fill('preview-secret')
  await page.getByRole('button', { name: 'Open workspace' }).click()
  await expect(page.getByRole('combobox', { name: 'Search products' })).toBeVisible()
  expect(consoleErrors).toEqual([])
})

function watchConsole(page: Page) {
  const errors: string[] = []
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', error => errors.push(error.message))
  return errors
}

async function assertNoOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}

async function capture(page: Page, name: string) {
  await page.screenshot({ path: test.info().outputPath(`${name}.png`), fullPage: true })
}

test('Search keyboard journey keeps reliable and degraded evidence distinct', async ({ page }) => {
  const consoleErrors = watchConsole(page)
  await installApi(page)
  await page.goto('/search')
  const search = page.getByRole('combobox', { name: 'Search products' })
  await search.fill('Batwing')
  const option = page.getByRole('option', { name: /Batwing/i })
  await expect(option).toBeVisible()
  await search.press('ArrowDown')
  await search.press('Enter')
  await expect(page.getByRole('heading', { name: 'Trustworthy price range' })).toHaveCount(2)
  await expect(page.getByText('Complete coverage')).toBeVisible()
  await expect(page.getByText('Partial catalog')).toBeVisible()
  await expect(page.getByText('Visible, excluded from the reliable range', { exact: true })).toBeVisible()
  await expect(page.getByText('Out of stock')).toBeVisible()
  await expect(page.getByText(/Price fell from \$60\.00 by \$15\.00/i)).toBeVisible()
  await expect(page.getByText('USD market')).toBeVisible()
  await expect(page.getByText('EUR market')).toBeVisible()
  await assertNoOverflow(page)
  await capture(page, 'search')
  expect(consoleErrors).toEqual([])
})

test('Export prepares and parses real CSV, JSON, and JSONL downloads', async ({ page }, testInfo) => {
  const consoleErrors = watchConsole(page)
  await installApi(page)
  await page.goto('/exports')
  await page.getByLabel('Competitor').selectOption('1')
  await page.getByLabel('Collection URL').fill('https://alpha.example/collections/murder-mystery-2')

  for (const format of ['csv', 'json', 'jsonl'] as const) {
    await page.getByLabel('Format').selectOption(format)
    await page.getByRole('button', { name: 'Prepare export' }).click()
    await expect(page.getByText('Complete catalog')).toBeVisible()
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: new RegExp(`Download ${format.toUpperCase()} export`, 'i') }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe(`alpha-market-murder-mystery-2-prices.${format}`)
    const path = await download.path()
    expect(path).not.toBeNull()
    const body = await readFile(path!, 'utf8')
    if (format === 'csv') {
      expect(body).toContain('Bátwing,45.125,USD')
    } else if (format === 'json') {
      expect(JSON.parse(body).items[0].price).toBe(45.125)
    } else {
      const lines = body.trim().split('\n').map(line => JSON.parse(line))
      expect(lines).toHaveLength(2)
      expect(lines[0].title).toBe('Bátwing')
    }
  }
  await assertNoOverflow(page)
  await capture(page, 'exports')
  expect(consoleErrors).toEqual([])
  expect(testInfo.project.name).toMatch(/desktop|tablet|mobile/)
})

test('Competitors reports HTTP 202 as queued, never complete', async ({ page }) => {
  const consoleErrors = watchConsole(page)
  await installApi(page)
  await page.goto('/competitors')
  await expect(page.getByRole('heading', { name: 'Competitor sync' })).toBeVisible()
  await expect(page.getByText('Partial').first()).toBeVisible()
  await page.getByRole('button', { name: /Sync all/i }).click()
  await expect(page.getByText('Sync request queued')).toBeVisible()
  await expect(page.getByText(/dispatch not requested/i)).toBeVisible()
  await expect(page.getByText(/Sync complete/i)).toHaveCount(0)
  await assertNoOverflow(page)
  await capture(page, 'competitors')
  expect(consoleErrors).toEqual([])
})

test('Navigation, focus, Escape, landmarks, and reduced motion remain operable', async ({ page }, testInfo) => {
  const consoleErrors = watchConsole(page)
  await installApi(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/search')
  await expect(page.getByRole('main')).toBeVisible()
  await expect(page.getByRole('navigation', { name: testInfo.project.name === 'mobile-390' ? 'Daily workflows' : 'Primary navigation' })).toBeVisible()
  if (testInfo.project.name === 'mobile-390') {
    await page.getByRole('button', { name: 'Open more navigation' }).click()
    const drawer = page.getByRole('dialog', { name: 'Application navigation' })
    await expect(drawer).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()
  }
  const input = page.getByRole('combobox', { name: 'Search products' })
  await input.focus()
  expect(await input.locator('xpath=..').evaluate(element => getComputedStyle(element).boxShadow)).not.toBe('none')
  expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true)
  await assertNoOverflow(page)
  expect(consoleErrors).toEqual([])
})

test('Search empty, API failure, and zero-reliable states are explicit', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-1440', 'resilience matrix runs once; viewport journeys run above')
  const consoleErrors = watchConsole(page)
  await installApi(page)
  await page.goto('/search')
  const input = page.getByRole('combobox', { name: 'Search products' })
  await input.fill('Missing')
  await expect(page.getByText(/No products matched/)).toBeVisible()
  expect(consoleErrors).toEqual([])
})

test('Export partial, suspicious-empty, failure, and cached states stay truthful', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-1440', 'truth-state matrix runs once; viewport journey runs above')
  for (const state of ['partial', 'suspicious_empty', 'failure'] as const) {
    await page.unrouteAll({ behavior: 'wait' })
    await installApi(page, { exportState: state })
    await page.goto('/exports')
    await page.getByLabel('Competitor').selectOption('1')
    await page.getByLabel('Collection URL').fill('https://alpha.example/collections/murder-mystery-2')
    await page.getByRole('button', { name: 'Prepare export' }).click()
    if (state === 'partial') {
      await expect(page.getByText('Partial live data')).toBeVisible()
      await expect(page.getByText(/Page limit reached/i)).toBeVisible()
      await expect(page.getByRole('button', { name: /Download partial JSONL export/i })).toBeVisible()
    } else if (state === 'suspicious_empty') {
      await expect(page.getByText('Suspicious empty result')).toBeVisible()
      await expect(page.getByText('LIVE', { exact: true })).toBeVisible()
      await expect(page.getByText('STORED', { exact: true })).toHaveCount(0)
    } else {
      await expect(page.getByText('Live acquisition failed')).toBeVisible()
      await expect(page.getByRole('button', { name: 'Prepare latest stored data' })).toBeVisible()
    }
  }

  await page.unrouteAll({ behavior: 'wait' })
  await installApi(page)
  await page.goto('/exports')
  await page.getByRole('radio', { name: /Latest stored data/i }).click()
  await page.getByLabel('Competitor').selectOption('1')
  await page.getByLabel('Collection URL').fill('https://alpha.example/collections/murder-mystery-2')
  await page.getByRole('button', { name: 'Prepare export' }).click()
  await expect(page.getByText('STORED', { exact: true })).toBeVisible()
  await expect(page.getByText(/Stored observations range/)).toBeVisible()
})
