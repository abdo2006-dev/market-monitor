# API Contracts

The Phase 0 inventory is retained for historical contract defects. Phase 1B.2 adds the
explicit durable Sync contracts in §1.1 and makes them the frontend authority.

---

## 1. Route inventory

The table below is the Phase 0 inventory. New Sync V2 routes follow it.

| Method | Path | Handler | `response_model` |
|---|---|---|---|
| GET | `/api/competitors` | `list_competitors` | `List[CompetitorOut]` |
| POST | `/api/competitors` | `create_competitor` | `CompetitorOut` |
| POST | `/api/competitors/scan-all` | `scan_all` | **none** |
| POST | `/api/competitors/seed-defaults` | `seed_default_competitors` | `List[CompetitorOut]` |
| GET | `/api/competitors/{competitor_id}` | `get_competitor` | `CompetitorOut` |
| PUT | `/api/competitors/{competitor_id}` | `update_competitor` | `CompetitorOut` |
| DELETE | `/api/competitors/{competitor_id}` | `delete_competitor` | none (204) |
| POST | `/api/competitors/{competitor_id}/scan-now` | `scan_now` | **none** |
| GET | `/api/products` | `list_products` | `dict` — untyped |
| GET | `/api/products/{product_id}` | `get_product` | `ProductOut` |
| GET | `/api/products/{product_id}/history` | `get_product_history` | `List[SnapshotOut]` |
| GET | `/api/events` | `list_events` | **none** |
| GET | `/api/search/products` | `search_products` | **none** |
| GET | `/api/search/suggestions` | `search_suggestions` | `SearchSuggestionsResponse` |
| GET | `/api/search/compare` | `compare_product` | `SearchCompareResponse` |
| GET | `/api/search/batch-compare` | `batch_compare_products_get` | **none** |
| POST | `/api/search/batch-compare` | `batch_compare_products` | **none** |
| GET | `/api/search/batch-compare-summary` | `batch_compare_summary_get` | **none** |
| GET | `/api/dashboard/summary` | `dashboard_summary` | **none** |
| GET | `/api/dashboard/sales-trends` | `sales_trends` | **none** |
| GET | `/api/settings` | `get_settings` | `AppSettingsOut` |
| PUT | `/api/settings` | `update_settings` | `AppSettingsOut` |
| GET | `/api/exports/collection-prices` | `export_collection_prices` | none (file response) |
| GET | `/api/cron/scan-due` | `scan_due` | **none** |
| GET | `/api/cron/daily-summary` | `daily_summary` | **none** |
| GET | `/api/cron/daily` | `daily` | **none** |

**14 of 26 routes declare no response model.** FastAPI documents those as untyped
objects in `openapi.json`, so they cannot be used to generate frontend types. This is the
blocking prerequisite for contract generation, and it is why that work is Phase 1 rather
than Phase 0.

`DashboardSummary` and `PaginatedResponse` are defined in
`backend/app/schemas/__init__.py` (`:165`, `:177`) and used by **no route**.
`PaginatedResponse.items` is `List[Any]` even so.

### 1.1 Durable Sync V2 contracts

| Method | Path | Response model | Semantics |
|---|---|---|---|
| POST | `/api/sync/competitors/{id}` | `SyncRequestStatus` | 202 after durable commit; optional `Idempotency-Key` (max 180 chars) |
| POST | `/api/sync/all` | `SyncRequestStatus` | 202 grouped request; one run per active competitor/reused active run |
| GET | `/api/sync/requests/{uuid}` | `SyncRequestStatus` | aggregate state plus per-run statuses |
| GET | `/api/sync/runs/{id}` | `SyncRunStatus` | one durable lifecycle record |
| GET | `/api/sync/freshness` | `list[CompetitorFreshness]` | minimum backend evidence for Phase 1C |

All POST responses set `Location: /api/sync/requests/{uuid}`. `status=queued` means the
request is durable, not that acquisition started or completed. `dispatch_status=failed`
means queued work remains recoverable; it is never converted to Sync failure/success.

`SyncRunStatus` exposes only safe fields: run/competitor identity, execution status,
trigger, lifecycle timestamps, attempt budget/retry time/lease expiry, failure category
and sanitized reason, product/page counts, strategy, completeness/cap evidence, and
duration. It does not expose claim tokens, worker credentials, storefront tokens, raw
responses, or stack traces.

`SyncRequestStatus.status` aggregates to `queued`, `running`, `retrying`, `success`,
`partial`, or `failed`. A successful execution with partial/suspicious coverage aggregates
as `partial` because it cannot establish complete market coverage.

The legacy `/api/competitors/.../scan-now` and `/scan-all` paths delegate to these use
cases under `SYNC_EXECUTION_MODE=v2`; their historical shapes below exist only when the
explicit rollback flag is `legacy`.

The compatibility `/api/cron/scan-due` and `/daily` paths create automatic V2 work only
when `SYNC_MORNING_ENABLED=true`. While the default-off rollout gate is false,
`scan-due` returns `{"execution":"durable_v2","status":"automatic_sync_disabled"}` and
does not create a request or run.

Existing product responses now add nullable `last_observed_at` and
`last_observed_run_id`. Snapshot/event responses add nullable observation/run lineage.
Legacy rows remain null; V2 reconciliation supplies real values. These additive fields are
the per-product freshness/provenance foundation for Phase 1C.

### 1.2 Phase 1C Search contracts

| Method | Path | Response model | Semantics |
|---|---|---|---|
| GET | `/api/search/suggestions` | `SearchSuggestionsResponse` | Grouped logical products, deterministic candidate-cap telemetry, and currency-separated observed lows. |
| GET | `/api/search/compare` | `SearchCompareResponse` | One row per active competitor plus trust, run evidence, direct price change, and backend-owned currency market summaries. |

`/compare` requires one target selector in normal UI use: `product_id` for a chosen
suggestion or `q` for server target selection. Supplying neither returns 422.

Each comparison row retains the existing nullable `product` shape and adds:

- `trust.coverage_state`, price reliability, exact observation/coverage timestamps and
  ages, latest partial/failure evidence, producing run, and active Sync;
- nullable direct-snapshot `price_change`;
- same-currency difference from `lowest_reliable_price`, when that reference exists.

`market_summary.currencies[]` owns lowest reliable, lowest observed, highest reliable,
median reliable, sellers, and counts. Search price fields serialize as JSON numbers to
preserve the established frontend contract. Currencies are separate entries and never
converted or compared. Full rules: `docs/SEARCH_ARCHITECTURE.md`.

The TypeScript functions and `MarketSearch.tsx` consume matching explicit types. This is
the smallest safe critical-route contract improvement; application-wide OpenAPI type
generation remains deferred until unrelated untyped routes are modeled.

### 1.3 Phase 1D Collection Export contract

`GET /api/exports/collection-prices` remains a file response, so it has no FastAPI JSON
`response_model`. Its query contract is `competitor_id`, absolute same-public-host
`collection_url`, `format=csv|jsonl|json`, `max_pages=1..20`, `mode=live|cached`, and
`include_provenance=false|true`. `mode=live` is the default and returns only this
request's acquisition. It does not fall back to stored products. `mode=cached` is the
only path that reads active stored rows and returns 404 `cached_export_unavailable` when
none match.

Successful files retain the established defaults: CSV fields/order, JSONL row shape, JSON
envelope, title sort, media type, and filename. File-level truth is carried in stable
`X-Market-Monitor-Export-*` headers, including provenance version, requested/actual
source, completeness, product/page count, cap evidence, safe reason, observation bounds,
coverage state, and genuine cached run IDs. `scraped_at` remains the file-generation time
for compatibility, not an observation timestamp.

`include_provenance=true` is an explicit additive extension. It adds `provenance` to the
JSON envelope and appends `observed_at`, `observed_run_id`, and `coverage_state` to CSV and
JSONL rows. Live acquisition failures are safe 502 JSON:
`{"detail":{"code":"live_acquisition_failed","message","provenance"}}`; they never
expose adapter detail or silently produce a cached file. The frontend uses
`prepareCollectionExport`, which requests a Blob and parses these headers/failures before
the user confirms download. `collectionPricesExportUrl` remains exported only for legacy
external callers and is not used by the Phase 1D page.

---

## 2. Known contract defects

### 2.1 Legacy-only: `POST /api/competitors/{id}/scan-now` has two old shapes

`api/competitors.py:152`:

```jsonc
// inline path (shopify_json / salla_json / RUN_SCANS_INLINE)
{ "message": "Scan completed",
  "result": { "status": "success", "products_found": 412,
              "new_products": 3, "price_changes": 11 } }

// queued path (everything else)
{ "message": "Scan queued", "task_id": "b1f2…" }
```

The caller cannot predict which. `frontend/src/lib/api.ts:24` copes with optional chaining:
`result.result?.status || (result.task_id ? 'queued' : 'completed')`.

Worse, `result` may be `null`: `_scrape_competitor_async` returns `None` when the
competitor is missing or inactive (`workers/tasks.py:42`), and that bare `return`
propagates into `{"message": "Scan completed", "result": null}` — a success message for a
scan that never ran.

### 2.2 Pagination is shaped consistently but typed nowhere

`/api/products`, `/api/events`, `/api/search/products` all return
`{items, total, page, page_size}`. None uses `PaginatedResponse`. `/api/search/products`
additionally returns `query`; `/api/search/suggestions` returns grouped items plus query
and explicit candidate-cap telemetry, with no paging.

`/api/products/{id}/history` returns a bare array, unpaginated and unbounded.

### 2.3 Filtering happens after pagination in legacy `/search/products`

`search_products` (`search_dashboard_settings.py:98`) applies `LIMIT 1000` in SQL, scores
in Python, drops anything below 0.34, and *then* paginates the survivors. `total` is the
count after filtering but the 1000-row SQL cap is invisible to the client, so results
silently truncate on large catalogues with no indication.

### 2.4 Prices cross the boundary as three different types

`Decimal` in the database → `Decimal` in `ProductOut.current_price` → JSON number.
`sales-trends` and `batch-compare-summary` explicitly cast to `float`. Phase 1C Search
response models also declare JSON-number prices to preserve its established TypeScript
contract. Other routes still differ, so the application-wide precision guarantee remains
inconsistent.

### 2.5 Error responses are unmodelled

Errors are FastAPI's default `{"detail": "..."}`. `detail` is a string for `HTTPException`
and a **list of objects** for 422 validation errors. `frontend/src/lib/api.ts:31` reads
`error?.response?.data?.detail` and renders it directly, so a validation error renders as
`[object Object]`.

### 2.6 `POST /api/competitors` silently rewrites the submitted payload

`_normalize_competitor_payload` (`api/competitors.py:185`) may change `scrape_type`,
clear `listing_urls`, and inject `selector_config` defaults. The response reflects the
rewritten entity, but nothing tells the user their configuration was overridden.

---

## 3. Frontend consumption

Single access point: `frontend/src/lib/api.ts`. The daily Sync functions have explicit
`SyncRequestStatus`/`SyncRunStatus`/`CompetitorFreshness` types. `scanAllCompetitors()` now
makes one backend request; no React/browser fan-out remains.

| Frontend function | Endpoint | Consumers |
|---|---|---|
| `getCompetitors` | GET `/competitors` | Competitors, Dashboard, Products, Activity, Exports, SalesTrends |
| `createCompetitor` / `updateCompetitor` / `deleteCompetitor` | POST/PUT/DELETE `/competitors` | Competitors |
| `seedDefaultCompetitors` | POST `/competitors/seed-defaults` | Competitors |
| `scanNow` | POST `/sync/competitors/{id}` | Competitors |
| `scanAllCompetitors` | POST `/sync/all` | Competitors |
| `getSyncRequest` / `getSyncRun` | GET `/sync/requests/*`, `/sync/runs/*` | Competitors polling/status |
| `getSyncFreshness` | GET `/sync/freshness` | Competitors; Phase 1C foundation |
| `getProducts` / `getProduct` / `getProductHistory` | `/products*` | Products, ProductDetail |
| `getEvents` | GET `/events` | Activity |
| `searchProducts` | GET `/search/products` | *unused* |
| `getSearchSuggestions` / `compareProduct` | `/search/*` | MarketSearch |
| `getDashboardSummary` | GET `/dashboard/summary` | Dashboard |
| `getSalesTrends` | GET `/dashboard/sales-trends` | SalesTrends |
| `collectionPricesExportUrl` | builds a URL string | Exports |
| `getSettings` / `updateSettings` | `/settings` | Settings |

Notes:

- `searchProducts` is exported and never called — `MarketSearch.tsx` uses
  `getSearchSuggestions` + `compareProduct` instead. Dead code.
- `/api/search/batch-compare*` has **no frontend consumer at all**. Three routes exist
  solely for external/manual use.
- `collectionPricesExportUrl` reads `api.defaults.baseURL` and hand-builds a query string,
  bypassing axios entirely so the browser can navigate to it for the download.
- Only `SalesTrends.tsx` declares response types (`:36-53`), hand-written and unverified
  against the backend. It is also the only page not using TanStack Query.

---

## 4. V2 target — contract safety

Recorded as `docs/adr/0005-generated-api-contracts.md`.

### 4.1 Make the OpenAPI schema complete

Prerequisite for everything else. Declare an explicit `response_model` on all 14 routes
that lack one. Model the shapes that already exist rather than changing them:

```python
class ScanRequestedOut(BaseModel):
    scrape_run_id: int
    competitor_id: int
    status: Literal["queued", "already_running"]

class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
```

This is additive and verifiable route by route: capture the current JSON response, add the
model, assert the response is byte-identical.

### 4.2 Generate TypeScript from the schema

`openapi.json` → `frontend/src/lib/api-types.ts` via `openapi-typescript` (a dev-only
dependency, no runtime cost). Generated file is committed so diffs are reviewable.

`api.ts` keeps its current shape — a thin, centralized module of named functions — but
each gains real parameter and return types drawn from the generated file. This preserves
the existing structure; it does not replace axios or TanStack Query.

### 4.3 Fail CI on drift

```
1. start the app, dump openapi.json
2. regenerate api-types.ts
3. git diff --exit-code   → non-zero means the committed types are stale
4. tsc --noEmit           → non-zero means the frontend does not match the new contract
```

A backend response change then breaks the build instead of breaking a page at runtime.

### 4.4 Eliminate the `any` that matters

Not all 47. In priority order:

1. `api.ts` — all 20 functions. Fixing this file alone types most call sites transitively.
2. `Dashboard.tsx:47`, `Activity.tsx:108`, `Products.tsx:79`, `MarketSearch.tsx:133` —
   `.map()` over API payloads.
3. `CompetitorForm.tsx:36-37` — the form's `onSubmit`/`initial`, which is where invalid
   competitor payloads originate.

Cosmetic `React.ChangeEvent<any>` in event handlers is low priority and may stay.

### 4.5 Fix the typecheck first

`npx tsc --noEmit` currently fails on `SalesTrends.tsx:218` (`replaceAll`, TS2550). None of
the above is enforceable until the typecheck passes and runs in CI. Addressed in Phase 0 —
see `docs/PROJECT_STATUS.md`.

### 4.6 Remove business orchestration from React — completed for Sync

`scanAllCompetitors` now calls `/api/sync/all`; the backend request use case determines
eligibility, deduplication, and grouping, while the page polls request/freshness status.

### 4.7 Organize the frontend by feature (unchanged)

Current layout is by technical kind (`pages/`, `components/`, `lib/`). Target:

```
src/
  features/
    competitors/   pages · components · hooks · api
    products/
    market-search/
    activity/
    exports/
    settings/
  shared/          ui/ · lib/ · types/
```

This is a mechanical move and should happen **after** contract typing, not before — moving
untyped files first makes the typing diff unreadable. The UI itself is not being
redesigned in Phase 1.

---

## 5. Phase 1A/1B.2 status and the generation plan

### 5.1 What Phase 1A did

**Investigated OpenAPI-generated TypeScript and deliberately deferred it.**

The blocking reason: **every Search endpoint, the Export endpoint, and `scan-now` declare
no `response_model`** (§1). FastAPI therefore emits them in `openapi.json` as untyped
objects. Running `openapi-typescript` today would generate `unknown` for exactly the
endpoints the daily workflows depend on — real machinery, no benefit, plus a new
dependency and a new CI step to maintain.

Instead, Phase 1A added **hand-written types for the critical path only**, in
`frontend/src/lib/types.ts`, each shape verified against the backend contract tests in
`backend/tests/critical/`. The file carries a header saying it is temporary and names its
replacement.

Typed in `frontend/src/lib/api.ts`: `getCompetitors`, `createCompetitor`,
`updateCompetitor`, `seedDefaultCompetitors`, `scanNow`, `scanAllCompetitors`,
`getSearchSuggestions`, `compareProduct`, `batchCompareSummary`,
`collectionPricesExportUrl`.

Still `any` (deliberately out of scope — not on the critical path): `getProducts`,
`getProduct`, `getProductHistory`, `getEvents`, `searchProducts`, `getDashboardSummary`,
`getSalesTrends`, `getSettings`, `updateSettings`.

`any` eliminated entirely from `MarketSearch.tsx`, `Exports.tsx`, and `Competitors.tsx`.

### 5.2 Contract bug found by typing — removed from V2 Sync

`ScanAllItem.status` mixes two vocabularies. The client-side fan-out assigns
`queued | completed | failed | skipped`, but `api.ts:55` passes through
`result.result?.status` when present — a `ScanResult` status, `success | failed`. A
successful inline scan therefore yields the literal `'success'`, which no consumer checks
for.

The V2 contract replaces this mixed vocabulary with explicit request and run states. The
old behavior remains characterized only for `SYNC_EXECUTION_MODE=legacy`.

Two latent null-dereferences were also surfaced and fixed: `selected` in
`MarketSearch.tsx:25` and `editing` in `Competitors.tsx:165`. Both were runtime-guarded by
a sibling prop (`enabled`, `open`) but unguarded in the callback itself.

### 5.3 Exact plan for Phase 1B/1C

Ordered. Each step is a prerequisite for the next.

**Step 1 — Snapshot the current responses (safety net).**
Before adding any `response_model`, capture the exact JSON each critical endpoint returns
today, as committed fixtures. `backend/tests/critical/` already asserts the field-level
shapes; extend to full-payload snapshots for `/search/suggestions`, `/search/compare`,
`/search/batch-compare-summary`, and `scan-now`.

*Why this matters:* `response_model` **filters** the response. A model that omits a field
silently drops it, and neither the compiler nor a smoke test would notice. The snapshot is
what makes step 2 safe, and it is why step 2 was not attempted in Phase 1A.

**Step 2 — Declare `response_model` on the critical-path routes.**
Model the shapes that already exist; change nothing. Assert each response is byte-identical
to its snapshot. Order: `scan-now` (smallest), `/search/suggestions`, `/search/compare`,
`/search/batch-compare-summary`, exports envelope.

Note `/search/compare` returns `Decimal` via `ProductOut.model_dump()` while `sales-trends`
and `batch-compare-summary` cast to `float`. Modelling will expose this; keep the existing
serialisation, do not "fix" it silently.

**Step 3 — Add `openapi-typescript` (dev dependency).**

```
npm i -D openapi-typescript
```

**Step 4 — Generate and commit.**

```
python -c "import json,app.main; print(json.dumps(app.main.app.openapi()))" > openapi.json
npx openapi-typescript openapi.json -o src/lib/api-types.ts
```

Commit `api-types.ts` so its diffs are reviewable.

**Step 5 — Replace `types.ts` with generated types.**
Delete the hand-written shapes as their generated equivalents land. Keep hand-written types
only for things the backend genuinely cannot express — for example the `ScanNowResponse`
union, until ADR 0003 collapses it.

**Step 6 — Fail CI on drift.**

```
dump openapi.json → regenerate api-types.ts → git diff --exit-code → tsc --noEmit
```

A stale committed type file or a frontend that no longer matches the contract breaks the
build instead of a page.

**Step 7 — Extend to the remaining 14 routes**, lowest risk last.
