# Daily Critical Workflows

The owner uses three workflows every day and makes real pricing decisions from
them. They are the highest-priority product surface, and all near-term
architecture work must protect or enable them:

1. **Market Search** — `/search`
2. **Collection Exports** — `/exports`
3. **Competitor price synchronisation** — "Scan" / "Scan All"

Everything else in the application (Dashboard, Activity, Discord, Settings,
Treasury Audit) is explicitly lower priority until these are reliable.

Written during Phase 1A. Source references are to commit `07f780b` + Phase 1A
changes.

---

## 1. Why these three are business-critical

They form one chain, and the chain is only as trustworthy as its weakest link:

```
Sync  →  populates products.current_price / last_checked_at
            │
            ├──→ Search   ("who is cheapest on Batwing right now?")
            └──→ Export   ("give me this whole collection as a spreadsheet")
```

**Search and Export are only as good as Sync.** A beautiful comparison table
built on three-day-old prices is worse than no table, because it looks
authoritative. This is why Phase 1B's objective is Sync reliability rather than
Search features.

---

## 2. Market Search

### How it works after Phase 1C

```
MarketSearch.tsx
  → debounced GET /api/search/suggestions?q=...  group candidate items
  → user picks one
  → GET /api/search/compare?product_id=...       compare + trust + market summary
```

Both are implemented in `api/search_dashboard_settings.py`, which also contains
the regression-protected identity/matching engine and a hardcoded Roblox vocabulary.
Pure daily-cycle trust classification is in `domain/search_trust.py`.

**Suggestions** loads up to 1,000 candidate rows via `ILIKE` on fuzzy
token prefixes, scores each in Python, drops anything below 0.34, then groups by
a computed market identity `(collection, base, mutation)`. Grouping is what makes
"Batwing at three stores" one row rather than three. The cap is now explicit in the
typed response/UI, candidate ordering is deterministic, and mixed currencies are not
numerically compared.

**Compare** picks a target product and uses a base-token SQL fast path for score-1 alias
matches. Every unresolved competitor falls back across all of its active products through
the unchanged collection/mutation/0.86 matcher. It keeps the best match per competitor and
returns `product: null` for unmatched active competitors.
It also attaches Sync coverage evidence, direct snapshot price-change context, and
currency-separated market statistics.

### Dependencies

- `products.active`, `current_price`, `normalized_title`, `category` — all
  written by Sync.
- The identity engine's hardcoded vocabulary: `COLLECTION_ALIASES`,
  `COLLECTION_LABELS`, `MUTATION_PHRASES`, `GENERIC_COLLECTION_TOKENS`.
- `scrape_runs` completeness/observation/terminal evidence and active state.
- `product_snapshots` direct change history.
- Nothing outside PostgreSQL; Search does not scrape.

### Phase 1C status and remaining limitations

| # | Status | Evidence |
|---|---|---|
| S1 | **Resolved.** Stored stale/partial/failed/legacy prices remain visible but are excluded from the reliable range and carry absolute observation/coverage ages. | freshness/summary Search tests |
| S2 | **Resolved for the common interactive path.** A 15,000-row exact-alias profile fell from 967.83 ms to 91.40 ms median. Unresolved competitors retain the complete fuzzy fallback. Batch-summary behavior is unchanged. | `docs/SEARCH_ARCHITECTURE.md` §6 |
| S3 | **Mitigated, not removed.** Suggestions still cap at 1,000, but the API/UI now disclose it and recommend a narrower query. | typed suggestion metadata |
| S4 | **Open.** Business vocabulary remains hardcoded/duplicated; Phase 1C did not risk a taxonomy migration. | `SEARCH_ARCHITECTURE.md` §8 |
| S5 | **Resolved.** Compare with neither `q` nor `product_id` returns 422. | `test_compare_with_no_arguments_is_rejected` |
| S6 | **Resolved for Search.** Suggestions/compare have Pydantic response models and matching TypeScript contracts; row trust is structured. | `docs/API_CONTRACTS.md` §1.2 |

### Regression protection

The expanded critical suite protects the original matching/grouping/batch behavior plus
collection separation, mixed currencies, current/partial/suspicious/failed/stale/legacy
evidence, active Sync, reliable versus observed statistics, stock eligibility, and direct
snapshot price changes. Frontend tests cover loading, success, degradation, no reliable
price, no suggestions, API error, active Sync, and truthful accepted-request wording.

The regression-protected matching scores and thresholds were not rewritten. A guarded
fast path resolves only definitive score-1 aliases; unresolved competitors still execute
the complete prior matcher.

Full semantics, measured performance, query plans, and limitations are in
`docs/SEARCH_ARCHITECTURE.md`.

---

## 3. Collection Exports

### How it works after Phase 1D

`/exports` now defaults to **Live current collection** and requires an explicit
selection for **Latest stored data**. A live request calls the shared
completeness-aware acquisition boundary, returns only its own observations, and
reports `complete`, `partial`, or `suspicious_empty` through typed file headers.
It never substitutes stored rows. A failed live request is a safe structured
502 and the UI offers retry or a separately requested stored export.

The browser prepares blob bytes before downloading, so it can show actual source,
product/page count, cap warning, observation timing, and cached row age range.
CSV/JSONL defaults retain their historical row shape and JSON retains its historical
envelope. `include_provenance=true` is the explicit additive metadata extension.
Full contract and limitations: `docs/EXPORT_ARCHITECTURE.md`.

### Regression protection

`tests/critical/test_export_regression.py` now covers live complete/partial/
suspicious-empty/failure, explicit cache/legacy cache/no-cache, compatibility of
all formats and filenames, optional provenance, safe URL validation, and
read-only behavior. `Exports.test.tsx` covers default live selection, loading,
complete/partial/stored/failure/no-cache states, confirmation download, and
accessible controls.

### Historical Phase 1A baseline (resolved by Phase 1D)

```
Exports.tsx builds a URL → browser navigates → file downloads
  → GET /api/exports/collection-prices?competitor_id&collection_url&format&max_pages
      → validate competitor (404)
      → _validate_collection_url: scheme http(s) AND host == competitor host   [SSRF guard]
      → narrow selector_config: discover_collections=False, include_all_products=False,
                                request_timeout_seconds=8, collection_handles=[handle]
      → scrape_competitor(...)                       ← LIVE scrape, inside the request
      → if empty: _saved_collection_products(...)    ← SILENT fallback to stored rows
      → serialise CSV / JSONL / JSON
```

Nothing is persisted: no `ScrapeRun`, no products, no events. Exports are
invisible to the dashboard and to any future rate control.

### Historical failure modes

| # | Failure | Evidence |
|---|---|---|
| E1 | **Cached data can masquerade as live data.** See §3.1. | `test_empty_scrape_silently_falls_back_to_saved_products` |
| E2 | **A partial scrape is indistinguishable from a complete one.** One page of three returns 200 with no completeness signal. | `test_partial_scrape_exports_whatever_was_returned` |
| E3 | **A scraper exception is unhandled** and escapes as a 500. Ironically this is the *only* way the user learns acquisition failed — an empty result is masked by E1. | `test_scraper_exception_is_not_handled_and_returns_500` |
| E4 | The live scrape runs **inside the HTTP request**, with an 8-second per-request timeout but no overall bound. On Vercel the whole request must finish within 300s. | `exports.py:104` |
| E5 | `scraped_at` is stamped with *now* for every row, including fallback rows. It records when the file was generated, not when the price was observed. | `exports.py:120` |

### 3.1 Export provenance — the product risk

**This is the most important finding in the export path.**

The UI presents this as a live collection scrape. When the live scrape returns
zero products, `exports.py:60` substitutes previously stored products. The
response is **byte-for-byte indistinguishable** from a successful live export:

- same HTTP status (200)
- same `Content-Type`
- same `Content-Disposition` filename
- same field set, same envelope keys
- `scraped_at` freshly stamped with the current time

Verified explicitly by `test_empty_scrape_silently_falls_back_to_saved_products`,
which asserts that the envelope keys are exactly
`{competitor, collection_url, products_count, items}` with no provenance field
anywhere, and that the filename matches a live export exactly.

The fallback is not itself wrong — serving something is often better than serving
nothing, and it was added deliberately (commit `f346f70`). What is wrong is that
**the caller cannot tell which happened.** The owner can price against month-old
data believing it is current.

Phase 1A did **not** change this behaviour; Phase 1D supersedes it with explicit
live/cached modes and versioned provenance.

#### Superseded Phase 1A proposal

Every acquisition-backed response gains an explicit provenance block. No
architecture may allow stale data to silently present as fresh.

```jsonc
{
  "provenance": {
    "status": "live",            // live | cached | partial | failed
    "observed_at": "2026-08-11T09:12:44Z",   // when the DATA was acquired
    "generated_at": "2026-08-11T09:12:45Z",  // when the FILE was produced
    "source": "shopify_products_json",       // which strategy actually worked
    "pages_fetched": 3,
    "expected_pages": 3,
    "age_seconds": 1,
    "warnings": []
  },
  "items": [...]
}
```

| status | meaning |
|---|---|
| `live` | Acquisition succeeded and is believed complete. |
| `partial` | Acquisition succeeded but was truncated (page cap hit, some targets failed). Items are real but incomplete. |
| `cached` | Acquisition returned nothing; items come from stored products. `observed_at` is the **oldest** `last_checked_at` among them. |
| `failed` | Acquisition errored and no usable fallback exists. HTTP 200 with zero items must never be used for this — return a structured error. |

Delivery requirements:

- CSV/JSONL cannot carry a JSON envelope, so provenance is also emitted as
  response headers (`X-Data-Provenance`, `X-Data-Observed-At`), and CSV gains a
  `data_status` and `observed_at` column per row.
- The filename must differ for non-live data (e.g.
  `alpha-store-mm2-prices-CACHED.csv`), because filenames outlive HTTP responses.
- The Exports UI must show the status before the download starts, and require
  explicit confirmation for `cached`.

### What Phase 1A verified

28 regression tests in `backend/tests/critical/test_export_regression.py`,
covering: unknown competitor 404; non-absolute/non-http/`javascript:` URLs
rejected; foreign host rejected (SSRF guard); `www.` accepted as same host;
`max_pages` bounds (0 and 21 rejected, 1 and 20 accepted) and forwarding; the
narrowed selector config; CSV/JSONL/JSON field correctness including price,
currency, stock status, URLs, image, category, SKU, external id; null price
serialising as empty rather than 0; title sort order; deterministic filenames for
all three formats; empty CSV retaining its header row; export persisting nothing;
partial scrape; empty scrape with and without saved products; fallback filtered by
collection alias; fallback excluding inactive products; fallback NOT engaging on a
successful scrape; and the unhandled scraper exception.

---

## 4. Competitor synchronisation (Sync)

### How it works today

With `SYNC_EXECUTION_MODE=v2` (the default), every permitted manual, bulk, and scheduled
request creates durable `SyncRequest`/`ScrapeRun` rows through `app.application.sync`.
Automatic morning producers additionally require the default-off
`SYNC_MORNING_ENABLED` rollout gate. The browser does not fan out. A provider-neutral
worker claims PostgreSQL work, performs acquisition
outside a long transaction, and reconciles with the Phase 1B.1 advisory lock.

```text
202 durable request -> queued -> running -> success/failed/retry_wait
                                      |
                                      +-> completeness: complete/partial/suspicious_empty
```

The prior `workers/tasks.py:_scrape_competitor_async` body is retained only when
`SYNC_EXECUTION_MODE=legacy` for production rollback and notification compatibility.

```
[TX1] INSERT ScrapeRun(status='running')
      scrape_competitor(...)                    ← network, no session needed but one is held
      reject if empty and not allow_empty_catalog
      detect_changes(session, competitor, products)
[TX2] ScrapeRun -> success/failed, Competitor.last_scan_*
      SELECT unsent events for THIS COMPETITOR (not this run)
      send Discord webhooks
[TX3] COMMIT the sent flags
```

`detect_changes` (`services/detection.py`) matches by URL, then by
`external_id`; never by title (`:42`, deliberate — preserve). Unseen products
increment `consecutive_misses` and deactivate at 3.

### Phase 1B.2 correctness guarantees

- A PostgreSQL partial unique index and request lock prevent overlapping non-terminal V2
  runs for one competitor; `FOR UPDATE SKIP LOCKED` plus a UUID claim token gives one
  legitimate lease owner.
- Lease heartbeats cover long acquisition. Expired attempts recover to `retry_wait`; the
  final expired attempt becomes `abandoned` with a safe failure event.
- Complete, newer coverage may increment misses. Partial/truncated, suspicious-empty, and
  failed results never count unobserved products as absent.
- Exactly 1,250 products across a configured five-page Shopify request is `partial`, not
  complete.
- Current price ordering uses server-captured page/batch `observed_at`, then run ID for
  ties. An earlier observation cannot become newer merely because its acquisition returns
  later; a wholly older complete acquisition is terminal `stale_skipped`.
- New events and snapshots reference the run that produced them.
- GitHub workflow dispatch is not completion. A dispatch failure is visibly queued and
  remains recoverable by the scheduled worker.

### Remaining/legacy failure modes

| # | Failure | Evidence |
|---|---|---|
| Y1 | **Resolved in Phase 1B.1.** Concurrent acquisition is allowed, but PostgreSQL serializes the whole reconciliation transaction per competitor. | `test_concurrent_scans_of_one_competitor_reconcile_once` |
| Y2 | **Resolved in Phase 1B.1.** PostgreSQL enforces canonical URL and product-level identity uniqueness; explicit remediation preserves existing history. | `test_database_rejects_duplicate_canonical_product_url`, `test_database_rejects_duplicate_product_identity_key` |
| Y3 | **Price-change thresholds are dead config.** `MIN_PRICE_CHANGE_AMOUNT` / `_PERCENTAGE` are settable in env, `.env.example`, and the Settings UI, and honoured nowhere. Detection uses a hardcoded 0.001 epsilon. | `test_tiny_price_difference_below_configured_threshold_still_fires` |
| Y4 | **A degraded scraper manufactures sales signals.** Stock is compared as a raw string, so `in_stock → unknown` emits `stock_out`, which `sales-trends` counts as an inferred sale. | `test_unknown_stock_status_is_treated_as_a_transition` |
| Y5 | **`scrape_failed` events are never marked notified**, accumulating forever. | `test_scrape_failed_event_is_never_marked_notified` |
| Y6 | **Inactive competitor returns `None`**, surfacing as `{"message": "Scan completed", "result": null}` — a success message for a scan that never ran. | `test_inactive_competitor_is_skipped_and_returns_none` |
| Y7 | Celery `max_retries=3` is unreachable — the body catches everything and returns `{"status": "failed"}`. | `docs/DATA_FLOW.md` Flow 7 |
| Y8 | On Vercel, non-JSON strategies take the `.delay()` path and **never run**, while the API returns a `task_id`. | `docs/DEPLOYMENT.md` §2 |

### Verified-good behaviour (must not regress)

- **An empty scrape is a failure, not an empty catalogue.** Products keep
  `active=True` and `consecutive_misses=0`. This is the single most valuable
  safety property in the sync path.
- **A repeated identical scan is idempotent**: no duplicate products, no
  re-emitted events, no extra snapshots.
- Matching by `external_id` survives a URL change without creating a duplicate.
- Identical titles at different URLs stay distinct.
- Deactivation requires exactly 3 consecutive misses.
- Snapshots record *changes*, not observations.

### What Phase 1A verified

32 regression tests in `backend/tests/critical/test_sync_regression.py`, plus 5 focused
product-integrity tests in `test_product_integrity.py`, run
against a real PostgreSQL database with only the network scraper replaced.

Phase 1B.1 specifically proves: same new observation concurrently creates one product;
the same changed price concurrently creates one event; duplicate canonical URLs and
Shopify variant-changing external IDs collapse once; a Shopify variant selection change
retains one parent product; an older-started acquisition finishing last cannot overwrite a
later-started committed success; a later failure does not suppress an older valid result
or erase an intervening successful watermark; URL changes retain history; a uniqueness
collision rolls back and retries the complete decision exactly once; database constraints
reject direct duplicates; and the former duplicate → false removal chain no longer
produces a removed signal in sales trends.

---

## 5. Data freshness (Part D)

The owner makes pricing decisions from this application. **A price without freshness
information is potentially misleading.** Phase 1B.2 persists the minimum evidence;
Phase 1C now applies it to Search.

### 5.1 What can be derived reliably today

| Signal | Source | Trustworthy? |
|---|---|---|
| Product last observed | `products.last_observed_at` + `last_observed_run_id` | **Yes for V2.** Server time at the product page/batch boundary; failed/unobserved results do not advance it. |
| Product last seen | `products.last_seen_at` | **Yes**, and distinct from the above — a missing product's `last_checked_at` advances while `last_seen_at` does not. |
| Observation time of the current price | `products.last_observed_at` | **Yes for V2.** The price was true at the adapter's server-clock observation boundary. |
| Price actually changed at | newest `product_snapshots.checked_at` | **Yes**, but only for products that have ever changed. |
| Last attempted sync (per competitor) | `competitors.last_scan_at` | **Yes** — written on both success and failure. |
| Last sync outcome | `competitors.last_scan_status` | **Yes** (`success` / `failed`). |
| Last *successful* sync (per competitor) | `MAX(scrape_runs.started_at WHERE status='success')` | **Yes**, derivable. Not denormalised. |
| Run in progress | non-terminal `scrape_runs.status` + lease | **Yes.** Expired claims are recovered at worker startup/drain. |
| Partial sync | `scrape_runs.completeness` | **Yes.** Independent from successful execution. |
| Never synced | `competitors.last_scan_at IS NULL` | **Yes.** |
| Which run produced history | nullable `scrape_run_id` on snapshots/events | **Yes for V2 history.** Legacy history remains honestly null. |

`GET /api/sync/freshness` exposes last complete observation, latest partial observation,
last failure, whether current coverage is complete, and the active run. It deliberately
does not invent age thresholds.

### 5.2 Implemented Search trust model

Search exposes competitor coverage as `current_complete`, `partial`,
`suspicious_empty`, `failed`, `stale`, or `unknown`. A non-terminal run is shown
independently as active Sync, because work in progress does not erase the prior evidence.

The accepted morning topology supplies the cadence: yesterday remains the required
catalog cycle until the 08:47 Cairo recovery schedule, then today's complete observation
is required. This replaces the rejected arbitrary minute threshold. Search still shows
the exact product and complete-catalog ages so the user can evaluate the verdict.

The cadence becomes an operational promise only after Phase 1E explicitly enables the
GitHub repository Actions variable. Vercel keeps its same-named application
flag false so the compatibility cron is not a second automatic Sync owner.

Only a current-complete product directly linked to the latest complete run, with a price,
currency, and confirmed in-stock state, participates in `lowest_reliable_price`. Every
other observed price stays visible and may become `lowest_observed_price`. Market
statistics never combine currencies.

Full rules: `docs/SEARCH_ARCHITECTURE.md` §3–4.

---

## 6. Measuring the real workload (Part E)

`backend/scripts/benchmark_scan.py` measures acquisition **without touching stored data**:
it loads only six competitor configuration fields in a PostgreSQL read-only transaction,
rolls that transaction back, calls `scrape_competitor` directly, and never runs detection,
creates a `ScrapeRun`, changes scan state, or sends a notification.

```bash
cd backend && .venv/bin/python scripts/benchmark_scan.py --max-pages 2
```

```bash
cd backend && .venv/bin/python scripts/benchmark_scan.py --json /tmp/bench.json
```

Per competitor it reports: strategy, whether a browser is required, listing-URL count,
wall-clock duration, products found/priced, catalog-page and HTTP request counts,
total/median/slowest network time, status/throttling/server-error counts, repeated GETs,
approximate Python allocation peak, and a duration bucket. It aggregates the serial cost.

Two honesty details worth knowing:

- Competitors returning **zero products** are bucketed
  `EMPTY (would fail a real sync)` and counted as failures, because the scraper
  swallows connection errors and returns `[]` rather than raising
  (`docs/SCRAPING_ARCHITECTURE.md` §1.7). A naive success count would be wrong.
- Output suppresses URL-bearing scraper logs and **never includes** the database URL,
  storefront/listing URLs, `selector_config`, tokens, or raw exception messages. The JSON
  contains non-sensitive configured competitor labels and is written mode `0600`.
- Request counts instrument aiohttp/httpx, not Playwright traffic. `tracemalloc` measures
  Python allocations rather than whole-process RSS. Completeness is not observable from
  the current scraper contract and is reported honestly as such.

**This makes real requests to real competitor sites. Never run it in CI**, and do
not loop it.

### 2026-08-11 real sequential result

One normal five-page-cap run used application pacing and competitor configuration. Raw
sanitized output remains outside the repository.

| Measure | Result |
|---|---:|
| Active competitors | 12 |
| Measured wall clock | 30.08 s |
| Sum of competitor durations | 30.04 s |
| Median competitor | 1.99 s |
| Slowest | BloxCrew, 8.23 s |
| Non-empty / empty / exception | 11 / 1 / 0 |
| Products with prices | 7,950 |
| HTTP requests / successful catalog pages | 65 / 49 |
| 429 / 5xx / measured request exceptions | 0 / 0 / 0 |
| Browser-required competitors | 0 |
| Maximum Python allocation peak | 17.4 MB |

Eleven competitors used Shopify HTTP acquisition and one used Salla HTTP acquisition.
Every non-empty competitor completed in under ten seconds. Shopbloxs returned empty after
eight HTTP requests and would be rejected by normal Sync. Three competitors returned
exactly 1,250 products (`5 pages * 250`) at that benchmark's explicit ceiling, so they may
be truncated; topology evidence must
not be mistaken for completeness evidence. This was one acquisition-only run and excludes
reconciliation, notification, and cold runner setup.

---

## 7. Deployment topology (Part F) — accepted and implemented

ADR 0008 selects **GitHub Actions + PostgreSQL durable jobs** for initial personal use and
the same provider-neutral worker CLI on **Railway** as the professional reliability
upgrade. GitHub has two Cairo-aware idempotent recovery opportunities; manual API dispatch
is optional and server-only. Live Export stays synchronous on Vercel. See
`docs/DEPLOYMENT.md` for security and migration details.

---

## 8. Acquisition boundary (Part G)

The single most useful conceptual split for these three workflows:

```
                    ┌──────────────────────────────────────────┐
competitor source → │ ACQUISITION                              │ → ProductObservation[]
                    │ "get current market observations"        │
                    │ no database, no events, no notifications │
                    └──────────────────────────────────────────┘
                                       │
                 ┌─────────────────────┴─────────────────────┐
                 ▼                                           ▼
   ┌──────────────────────────┐                ┌──────────────────────────┐
   │ SYNC (persistence)       │                │ EXPORT (presentation)    │
   │ observations → reconcile │                │ observations → serialise │
   │   → PostgreSQL           │                │   → download             │
   └──────────────────────────┘                └──────────────────────────┘
                 │
                 ▼
   ┌──────────────────────────┐
   │ SEARCH (presentation)    │
   │ PostgreSQL → match/rank  │
   └──────────────────────────┘
```

Why this boundary specifically: **Sync and Export both need acquisition, and only
Sync needs persistence.** Today Export reaches into `scrape_competitor` directly
(`exports.py:56`) and Sync reaches into it from a worker, so the two share code
by coincidence rather than contract. That is also why Export has no `ScrapeRun`,
no provenance, and no rate control — it bypasses everything Sync built.

Phase 1B.2 implements the Sync side as `AcquisitionResult` around the existing scraper.
The full adapter protocol/extraction remains a later refactor; Export does not yet consume
the new boundary.

```python
class MarketDataAcquirer(Protocol):
    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult: ...

@dataclass(frozen=True)
class AcquisitionResult:
    observations: list[ProductObservation]
    status: AcquisitionStatus      # LIVE | PARTIAL | FAILED
    strategy_used: str
    pages_fetched: int
    observed_at: datetime
    warnings: list[str]
```

`AcquisitionResult.status` is what makes the export provenance contract in §3.1
implementable, and `strategy_used` is the diagnostic the current implementation
throws away.

Phase 1B.2 introduced the immutable `AcquisitionResult` boundary and adapter telemetry for
Sync without pretending the monolithic scraper is already split. Phase 1D consumes that
boundary for Live Export; a future refactor can extract typed adapters/`ProductObservation`.

---

## 9. Regression coverage summary

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test \
  .venv/bin/python -m pytest tests/ -q -m critical
```

| Suite | Tests | Covers |
|---|---|---|
| `test_schema_authority.py` | 10 | Alembic is the sole schema authority |
| `test_sync_regression.py` | 32 | reconciliation, idempotency, failure, concurrency and ordering |
| `test_product_integrity.py` | 5 | identity contract, constraints, audit and consolidation |
| `test_sync_lifecycle.py` | 32 | durable requests/claims/leases/retries, completeness, freshness, lineage, API/worker |
| `test_search_regression.py` | 35 | matching/guarded fallback/grouping, trust, currencies, reliable/observed summaries, snapshots, active Sync |
| `test_export_regression.py` | 24 | validation, formats, live/cached truth, completeness, provenance, lineage |
| **critical total** | **138** | plus 71 non-critical tests = **209 backend tests** |

The frontend adds 16 Vitest/Testing Library cases for the daily Search and Export
interactions.

Database-backed tests **skip** when `TEST_DATABASE_URL` is unset, and CI fails if
that happens there (`.github/workflows/ci.yml`).

No test in any of these suites makes a live network request.
