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

### How it works today

```
MarketSearch.tsx
  → GET /api/search/suggestions?q=...        group candidate items
  → user picks one
  → GET /api/search/compare?product_id=...   compare across competitors
```

Both are implemented in `api/search_dashboard_settings.py`, which also contains
the entire identity/matching engine (`:606`–`:811`) and a hardcoded Roblox
vocabulary (`:33`–`:93`).

**Suggestions** (`:150`) loads up to 1,000 candidate rows via `ILIKE` on fuzzy
token prefixes, scores each in Python, drops anything below 0.34, then groups by
a computed market identity `(collection, base, mutation)`. Grouping is what makes
"Batwing at three stores" one row rather than three.

**Compare** (`:495`) picks a target product, then loads **every active product in
the database** and scores each against the target, keeping the best match per
competitor above a 0.86 threshold. Competitors with no match are returned with
`product: null` so the UI can show full coverage.

### Dependencies

- `products.active`, `current_price`, `normalized_title`, `category` — all
  written by Sync.
- The identity engine's hardcoded vocabulary: `COLLECTION_ALIASES`,
  `COLLECTION_LABELS`, `MUTATION_PHRASES`, `GENERIC_COLLECTION_TOKENS`.
- Nothing else. Search performs no I/O beyond PostgreSQL.

### Known failure modes

| # | Failure | Evidence |
|---|---|---|
| S1 | **Search silently reflects stale data.** No freshness filter, no staleness signal in the response. A month-old price ranks above a fresh one purely by being lower. | `test_compare_ignores_freshness` |
| S2 | **Compare loads the entire active product table per query**, then scores in Python (`:519`). Batch compare calls it per query, so N queries = N full scans. Mitigated for batch by `_batch_compare_summary_response` loading once (`:288`), but `/search/compare` itself is unmitigated. | `:519`, `:271` |
| S3 | **Search truncates at 1,000 rows before scoring** (`:128`, `:602`) with no indication. On a larger catalogue, relevant items are silently dropped. | `:602` |
| S4 | **Business vocabulary is hardcoded in source.** Adding a new Roblox game requires a code change and a deploy, in three separate files. | `:33`–`:93`, `scraper.py:942`, `exports.py:170` |
| S5 | `/search/compare` has no required parameter; calling it with neither `q` nor `product_id` returns `200` with an empty envelope instead of `422`. | `test_compare_with_no_arguments_returns_empty_envelope` |
| S6 | Unmatched-competitor rows carry `match_score: 0` and `product: null`, but matched rows nest the product under `product` — the two shapes differ and are untyped on the frontend. | `docs/API_CONTRACTS.md` §2 |

### What Phase 1A verified

23 regression tests in `backend/tests/critical/test_search_regression.py`, all
passing, covering: exact name, imperfect spelling (`batwng`, `Batwin`), case and
punctuation normalisation, mutation variants kept separate, the same product
across three competitors grouped into one suggestion, best-price selection,
null-price handling, inactive products excluded, inactive competitors excluded,
cross-collection matches blocked, unrelated products not merged, no-results,
empty query rejected, compare-by-id, batch compare (JSON/markdown/CSV), the
100-query cap, and the freshness blind spot.

**The matching algorithm was deliberately not changed.** These tests characterise
it so that a future change is visible.

---

## 3. Collection Exports

### How it works today

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

### Known failure modes

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

Phase 1A does **not** change this behaviour. It is characterised and documented.

#### Proposed provenance contract (Phase 1D)

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

`workers/tasks.py:_scrape_competitor_async` is the one authoritative
reconciliation body — and it is called directly by the API layer as well as by
Celery (ARCHITECTURE A-4).

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

### Known failure modes

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

The owner makes pricing decisions from this application. **A price without
freshness information is potentially misleading**, and today no surface carries
one.

### 5.1 What can be derived reliably today

| Signal | Source | Trustworthy? |
|---|---|---|
| Product last checked | `products.last_checked_at` | **Yes.** Set on every reconciliation pass, whether or not anything changed. |
| Product last seen | `products.last_seen_at` | **Yes**, and distinct from the above — a missing product's `last_checked_at` advances while `last_seen_at` does not. |
| Observation time of the current price | `products.last_seen_at` | **Approximately.** The price was true as of the last sighting. |
| Price actually changed at | newest `product_snapshots.checked_at` | **Yes**, but only for products that have ever changed. |
| Last attempted sync (per competitor) | `competitors.last_scan_at` | **Yes** — written on both success and failure. |
| Last sync outcome | `competitors.last_scan_status` | **Yes** (`success` / `failed`). |
| Last *successful* sync (per competitor) | `MAX(scrape_runs.started_at WHERE status='success')` | **Yes**, derivable. Not denormalised. |
| Run in progress | `scrape_runs.status = 'running'` | **No.** There is no reaper, so a crashed process leaves a permanent `running` row. Callers work around it with a 30-minute cutoff. |
| Partial sync | — | **Not representable.** A truncated scrape is recorded as `success`. |
| Never synced | `competitors.last_scan_at IS NULL` | **Yes.** |
| Which run produced a price | — | **Not representable.** No `scrape_run_id` on products, snapshots, or events. |

**Summary: per-product and per-competitor freshness is already derivable and
trustworthy. In-progress and partial states are not.**

### 5.2 Minimal freshness model (target)

Deliberately small. Thresholds are **not** invented here — see §5.3.

```python
class Freshness(StrEnum):
    FRESH        # last successful sync within the competitor's expected interval
    STALE        # older than that, by a configurable multiple
    SYNCING      # a non-terminal ScrapeRun exists right now
    PARTIAL      # last run succeeded but acquisition was known-incomplete
    FAILED       # last run failed; the displayed price predates the failure
    NEVER        # never synced
```

Derivation, per competitor:

| State | Rule |
|---|---|
| `NEVER` | `last_scan_at IS NULL` |
| `SYNCING` | a `ScrapeRun` in a non-terminal state exists *(needs the state machine from ADR 0003)* |
| `FAILED` | `last_scan_status = 'failed'` |
| `PARTIAL` | latest successful run flagged incomplete *(needs a new column)* |
| `STALE` | `now - last_successful_sync > stale_after` |
| `FRESH` | otherwise |

Per product, freshness is the **worse** of its competitor's state and its own
`last_checked_at` age — a product can be stale even when its competitor synced
successfully, if it was missing from recent scans.

**Required new fields** (Phase 1B/1C, each needing a migration):

- `scrape_runs.trigger` and a real status enum with non-terminal states (ADR 0003)
- `scrape_runs.was_complete` (boolean) to represent PARTIAL
- `events.scrape_run_id`, `product_snapshots.scrape_run_id` for attribution
- optionally denormalised `competitors.last_successful_scan_at` for cheap reads

**Where it surfaces:** Search compare rows, Export provenance (§3.1), and the
competitor list. Search is the priority — that is where decisions are made.

### 5.3 Do not invent thresholds yet

"Stale" is meaningless without knowing the real sync cadence. `scan_frequency_minutes`
defaults to 60, but the Vercel deployment runs one cron a day, so the *effective*
cadence is 24h regardless of configuration.

Deciding `stale_after` requires: the deployment topology decision (§7), and real
scan durations from the benchmark (§6). Until both exist, express freshness as an
**absolute age** ("checked 3 hours ago") rather than a judgement ("stale").
Showing the number is honest; showing a verdict derived from a guessed threshold
is not.

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
exactly 1,250 products (`5 pages * 250`), so they may be truncated; topology evidence must
not be mistaken for completeness evidence. This was one acquisition-only run and excludes
reconciliation, notification, and cold runner setup.

---

## 7. Deployment topology (Part F) — proposal ready

The current matrix and recommendation are in ADR 0008. It proposes a ~$5/month persistent
Railway Python worker polling durable PostgreSQL runs, with public GitHub Actions as the
$0 fallback. Live Export stays synchronous on Vercel. The ADR remains **Proposed** pending
the owner's cost/reliability choice; no worker, endpoint, lifecycle, or topology was
implemented in this closeout.

---

## 8. Target architecture: acquisition boundary (Part G)

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

Proposed interface — the exact shape must follow repository needs, not this
sketch:

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

**Phase 1A deliberately did not create these types.** Introducing an interface
before the scraper is split (ADR 0004) would add a layer without removing one.
The sequence is: capture fixtures → extract `ShopifyAdapter` → introduce
`ProductObservation` → then `MarketDataAcquirer` over the adapters → then rewire
Export and Sync to consume it. See `docs/ROADMAP.md`.

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
| `test_search_regression.py` | 23 | matching, grouping, best price, freshness blind spot |
| `test_export_regression.py` | 28 | validation, formats, fields, fallback provenance |
| **total** | **98** | plus the 65 pre-existing unit tests = **163** |

Database-backed tests **skip** when `TEST_DATABASE_URL` is unset, and CI fails if
that happens there (`.github/workflows/ci.yml`).

No test in any of these suites makes a live network request.
