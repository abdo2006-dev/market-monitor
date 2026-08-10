# API Contracts

Baseline `f346f70`. The route table below was generated from the live FastAPI application
(`app.main.app.routes`), not transcribed by hand.

---

## 1. Route inventory

26 application routes plus 5 framework routes (`/docs`, `/docs/oauth2-redirect`,
`/openapi.json`, `/redoc`, `/health`).

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
| GET | `/api/search/suggestions` | `search_suggestions` | **none** |
| GET | `/api/search/compare` | `compare_product` | **none** |
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

---

## 2. Known contract defects

### 2.1 `POST /api/competitors/{id}/scan-now` returns two different shapes

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
additionally returns `query`; `/api/search/suggestions` returns `{items, total, query}`
with no paging at all.

`/api/products/{id}/history` returns a bare array, unpaginated and unbounded.

### 2.3 Filtering happens after pagination in search

`search_products` (`search_dashboard_settings.py:98`) applies `LIMIT 1000` in SQL, scores
in Python, drops anything below 0.34, and *then* paginates the survivors. `total` is the
count after filtering but the 1000-row SQL cap is invisible to the client, so results
silently truncate on large catalogues with no indication.

### 2.4 Prices cross the boundary as three different types

`Decimal` in the database → `Decimal` in `ProductOut.current_price` → JSON number.
But `sales-trends` (`:1004`) and `batch-compare-summary` (`:357`) explicitly cast to
`float`, and `compare` returns `ProductOut.model_dump()` which keeps `Decimal`. The
frontend receives numbers either way and treats them all as `number` — but the precision
guarantee differs by endpoint.

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

Single access point: `frontend/src/lib/api.ts`. All 20 exports. **No function declares a
return type**; 12 take `any` parameters.

| Frontend function | Endpoint | Consumers |
|---|---|---|
| `getCompetitors` | GET `/competitors` | Competitors, Dashboard, Products, Activity, Exports, SalesTrends |
| `createCompetitor` / `updateCompetitor` / `deleteCompetitor` | POST/PUT/DELETE `/competitors` | Competitors |
| `seedDefaultCompetitors` | POST `/competitors/seed-defaults` | Competitors |
| `scanNow` | POST `/competitors/{id}/scan-now` | Competitors, `scanAllCompetitors` |
| `scanAllCompetitors` | — **client-side fan-out**, not an endpoint | Competitors |
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

### 4.6 Remove business orchestration from React

`scanAllCompetitors` (`api.ts:14-56`) moves to the backend as `ScanAllCompetitors`. The
frontend gets `requestBulkScan()` returning run ids, and polls. See ARCHITECTURE A-3.

### 4.7 Organize the frontend by feature

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
