# Current System (as-built)

Audit of the application as it exists at baseline commit `f346f70`
("Fallback collection exports to saved products"), archived as tag
`archive/pre-v2-rearchitecture`.

Everything here was read from source. Where a claim is testable it was tested; the
verification method is stated inline. This document describes the system **as it is**,
including its defects. For the intended future shape see `docs/ARCHITECTURE.md`.

---

## 1. Shape of the repository

```
backend/          FastAPI + SQLAlchemy(async) + Celery + Playwright
  app/
    api/          6 route modules
    services/     scraper, detection, notification, default_competitors
    workers/      celery_app, tasks
    models/       6 SQLAlchemy models in one file
    schemas/      Pydantic models in one file
    utils/        price_parser, text_normalizer
  alembic/        2 migrations
  tests/          1 file, 65 tests
  main.py         Vercel ASGI shim
frontend/         React 18 + Vite + TanStack Query + axios, 9 pages
docker-compose.yml  postgres, redis, backend, celery_worker, celery_beat, frontend
vercel.json       serverless deployment + daily cron
```

Tracked application source is ~7,400 lines. Two files dominate:
`services/scraper.py` (1,195 lines) and `api/search_dashboard_settings.py` (1,058 lines).

---

## 2. Backend API surface

31 routes are registered (verified: `len(app.main.app.routes)` == 31, which includes
FastAPI's built-in docs/openapi routes).

### `api/competitors.py` — prefix `/api/competitors`

| Method | Path | Notes |
|---|---|---|
| GET | `""` | List all competitors, newest first. |
| POST | `""` | Create. Payload is rewritten by `_normalize_competitor_payload` (`:185`). |
| POST | `/seed-defaults` | Inserts 6 hardcoded Roblox storefronts from `services/default_competitors.py`. |
| POST | `/scan-all` | Mixed queue/inline fan-out. See §6. |
| GET | `/{competitor_id}` | Fetch one. |
| PUT | `/{competitor_id}` | Update; also passes through `_normalize_competitor_payload`. |
| DELETE | `/{competitor_id}` | Cascades to products, events, scrape runs. |
| POST | `/{competitor_id}/scan-now` | Inline or queued depending on `scrape_type`. See §6. |

`_normalize_competitor_payload` silently rewrites what the user submitted: a competitor
with no `listing_urls` and a `generic_selector`/`custom` type is converted to
`shopify_json` (`:193`), and a URL that "looks like" a Salla catalog is converted to
`salla_json` (`:190`). The UI does not surface that this happened.

Route ordering matters here: `/scan-all` (`:52`) is declared before `/{competitor_id}`
(`:121`). If it were declared after, `scan-all` would be captured by the path parameter.
Commit `cb3f52a` ("Fix scan all route ordering") is that bug being fixed once already.

### `api/products.py` — prefix `/api/products`

`GET ""` (filter/sort/paginate), `GET /{id}`, `GET /{id}/history` (all snapshots ascending,
unbounded — no pagination or date window).

### `api/events.py` — prefix `/api/events`

`GET ""` with filters. Note `:60` overrides the joined `Product.category` with the
category embedded in the event's JSON `new_value`/`old_value`, falling back to the live
product row. Events therefore report the category *at detection time*, which is
deliberate but undocumented anywhere else.

### `api/exports.py` — prefix `/api/exports`

`GET /collection-prices` — scrapes a single collection **synchronously inside the HTTP
request** and streams CSV/JSONL/JSON. `_validate_collection_url` (`:203`) enforces that
the requested host matches the competitor's host, which is a real SSRF guard. If the live
scrape returns nothing, it falls back to previously saved products
(`_saved_collection_products`, `:145`).

### `api/search_dashboard_settings.py` — three routers in one 1,058-line module

- `/api/search/products`, `/suggestions`, `/compare`, `/batch-compare` (GET+POST),
  `/batch-compare-summary` (json/markdown/csv)
- `/api/dashboard/summary`, `/sales-trends`
- `/api/settings` (GET, PUT)

This module contains the entire product-identity and market-comparison engine
(`:606`–`:811`) plus a hardcoded Roblox domain vocabulary (`:33`–`:93`).

### `api/cron.py` — prefix `/api/cron`

`GET /scan-due`, `GET /daily-summary`, `GET /daily`. Guarded by `_check_auth` (`:15`),
which compares `Authorization` against `settings.CRON_SECRET` — **but only if
`CRON_SECRET` is set**. It defaults to `None` (`config.py:26`), in which case the
endpoints are fully public. `vercel.json` schedules `/api/cron/daily` at 08:00 UTC.

---

## 3. Persistence

Six tables, defined in `backend/app/models/__init__.py`.

### `competitors`
`id, name, base_url, category, active, scan_frequency_minutes, scrape_type, listing_urls
(JSON), selector_config (JSON), discord_webhook_url, notes, last_scan_at,
last_scan_status, created_at, updated_at`

`scrape_type` is a free-text `String(50)` with no constraint. Values actually handled:
`shopify_json`, `salla_json`, `generic_selector`, `custom`.

### `products`
`id, competitor_id →competitors, external_id, identity_key, title, normalized_title,
category, url, canonical_url, image_url, current_price Numeric(12,2), currency,
stock_status, sku, first_seen_at, last_seen_at, last_checked_at, active,
consecutive_misses`

Indexes: `competitor_id`, `normalized_title`, `url`, `category`, `active`.
Phase 1B.1 adds database invariants on `(competitor_id, canonical_url)` and the non-null
`(competitor_id, identity_key)`. Raw URL/external ID remain source evidence. Product
identity and its conservative canonicalization contract are defined in
`domain/product_identity.py` and ADR 0007.

### `product_snapshots`
`id, product_id →products, title, category, price, currency, stock_status, image_url,
checked_at`. Append-only price/stock history. Written only when something changed
(`detection.py:183`), plus once at product creation.

### `events`
`id, competitor_id, product_id (SET NULL), event_type, old_value (JSON), new_value (JSON),
event_message, detected_at, notification_sent, notification_sent_at`

`event_type` is free text. Values produced: `new_product`, `price_increase`,
`price_decrease`, `price_changed`, `stock_in`, `stock_out`, `product_removed`,
`scrape_failed`. There is no `scrape_run_id` column — **events cannot be attributed to
the run that produced them**, which is the root of the notification defect in §7.

### `scrape_runs`
`id, competitor_id, started_at, finished_at, status, products_found, new_products_count,
price_changes_count, error_message`. Status values: `running`, `success`, `failed`.
No index on `status` or `started_at` despite both being queried together on every
scan-eligibility check.

### `app_settings`
Single row, `id=1`. **Written by the API and read by nothing.**

Verified by grep: the only references to the `AppSettings` model outside its own
definition are the two settings endpoints at `api/search_dashboard_settings.py:1033` and
`:1045`. All scan behaviour reads `app.config.settings` (environment) instead — see
`workers/tasks.py:69-72`. The Settings page in the UI therefore persists values that
never affect the application.

### Migrations

Two: `0001_initial`, `0002_product_category`. Head is `0002_product_category`.

---

## 4. Schema is defined twice, and the two definitions disagree

`backend/app/database.py:53` runs on every FastAPI startup:

```python
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE product_snapshots ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
```

**Verified experimentally** against PostgreSQL 16 (temporary container, two databases —
one built by `alembic upgrade head`, one by `init_db()`):

| | Alembic-built DB | `create_all`-built DB |
|---|---|---|
| `alembic_version` table | present, `0002_product_category` | **absent** |
| `product_snapshots` indexes | `ix_snapshots_checked_at`, `ix_snapshots_product_id` | `ix_product_snapshots_checked_at`, `ix_product_snapshots_id`, `ix_product_snapshots_product_id` |

Two consequences, both live:

1. A database created by application startup has **no Alembic version stamp**. The next
   `alembic upgrade head` will attempt to run `0001_initial` against a populated database
   and fail on `CREATE TABLE ... already exists`. `docker-compose.yml:57` runs
   `alembic upgrade head` before `uvicorn`, so in Compose the ordering happens to save
   you; on Vercel, where only the app runs, it does not.
2. Index names differ between the two paths, so a migration that drops an index by name
   will succeed on one deployment and fail on the other.

`alembic revision --autogenerate` against the migrated database confirms the drift
independently (reports the snapshot indexes as removed/added).

---

## 5. Configuration

`backend/app/config.py` defines 20 settings. Grep for actual use outside `config.py`:

| Setting | Read by application code? |
|---|---|
| `DATABASE_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | yes |
| `USER_AGENT`, `PLAYWRIGHT_HEADLESS`, `DEFAULT_MAX_PAGES`, `DEFAULT_PAGE_DELAY_SECONDS` | yes |
| `DISCORD_NOTIFICATIONS_ENABLED`, `DISCORD_DEFAULT_WEBHOOK_URL` | yes |
| `RUN_SCANS_INLINE` | yes (2 sites) |
| `CRON_SECRET` | yes (1 site) |
| `SECRET_KEY` | **no** |
| `REDIS_URL` | **no** (Celery uses the broker URLs) |
| `DEFAULT_TIMEZONE`, `DEFAULT_CURRENCY` | **no** |
| `DEFAULT_SCAN_INTERVAL_MINUTES` | **no** (per-competitor value used instead) |
| `MIN_PRICE_CHANGE_AMOUNT`, `MIN_PRICE_CHANGE_PERCENTAGE` | **no** |
| `IGNORE_KEYWORDS` | **no** |
| `DAILY_SUMMARY_ENABLED`, `DAILY_SUMMARY_TIME` | **no** |

So price-change thresholds and keyword filters are configurable in three places
(env, `.env.example`, the Settings UI) and honoured in none. `detection.py:117` treats any
difference above 0.001 as a price change.

`DAILY_SUMMARY_TIME` is likewise inert: the Celery beat entry (`workers/celery_app.py:31`)
is a fixed `86400.0`-second interval anchored to beat start, not a wall-clock time.

`Settings.Config.env_file = ".env"` is resolved relative to the working directory. The
only `.env` in the repository is at the repository root, but the backend runs from
`backend/`, so **the `.env` file is not loaded when running the backend locally** — the
class defaults apply instead.

---

## 6. Scan execution — five distinct pathways

This is the central architectural problem. The same logical operation is reachable five
ways, with different behaviour on each.

| # | Entry point | Mechanism | Concurrency guard |
|---|---|---|---|
| 1 | `POST /competitors/{id}/scan-now` (`api/competitors.py:152`) | Inline `await _scrape_competitor_async(id)` if `scrape_type` is `shopify_json`/`salla_json` or `RUN_SCANS_INLINE`; else `.delay()` | **none** |
| 2 | `POST /competitors/scan-all` (`api/competitors.py:52`) | Splits competitors into inline (JSON types) and queued; runs inline set with `asyncio.Semaphore(4)` inside the request | `_has_recent_running_scan` (`:171`) |
| 3 | `GET /cron/scan-due` (`api/cron.py:20`) | Sequential inline `await _scrape_competitor_async(id)` per due competitor | inline query at `:37` |
| 4 | Celery beat → `check_and_schedule_scans` (`workers/tasks.py:163`) | `.delay()` per due competitor | inline query at `:189` |
| 5 | Frontend `scanAllCompetitors` (`frontend/src/lib/api.ts:14`) | Client-side worker pool, concurrency 4, calling pathway #1 per competitor | **none** |

Pathway #5 is what the UI actually uses. `Competitors.tsx:36` calls
`scanAllCompetitors(competitors)`, not the backend `/scan-all` endpoint. **The backend
`/scan-all` endpoint is therefore dead code from the UI's perspective** while remaining a
public route. Commit history shows this was a deliberate move (`3622524`, "Run scan all
as client-side parallel scans"), which means the concurrency policy for scan-all now
lives in the browser and cannot be enforced server-side.

The three "is a scan already running?" checks (#2, #3, #4) are all the same
read-then-act pattern with no lock:

```python
cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
... select ScrapeRun where competitor_id=? and status='running' and started_at > cutoff
```

Two callers can both read "not running" and both proceed. Pathways #1 and #5 do not check
at all. Duplicate concurrent scans of the same competitor are possible today.

---

## 7. Scan lifecycle as implemented

`workers/tasks.py:_scrape_competitor_async` (`:25`) is the one shared body, and it is
invoked directly by the API layer as well as by Celery.

```
1  open AsyncSessionLocal session
2  load Competitor; return early if missing/inactive
3  INSERT ScrapeRun(status='running');  COMMIT          <- txn 1
4  detect whether any Product exists (is_initial_scan)
5  build a plain-dict copy of the competitor
6  await scrape_competitor(...)                          <- network I/O, no txn
7  reject the result if it is empty and allow_empty_catalog is not set
8  await detect_changes(session, competitor, products)   <- mutates products/snapshots/events
9  update ScrapeRun + Competitor status;  COMMIT         <- txn 2
10 SELECT events WHERE competitor_id=? AND notification_sent=false
11 send Discord webhooks for those events
12 COMMIT                                                <- txn 3
```

Problems visible in that sequence:

- **Step 10 is scoped to the competitor, not the run.** Because `events` has no
  `scrape_run_id`, a scan picks up every unsent event for that competitor, including
  events created by a *concurrent* scan that has not finished notifying yet. Two
  overlapping scans will send overlapping notification sets.
- **Step 11 sends before step 12 persists.** `dispatch_event_notifications`
  (`services/notification.py:188`) marks `notification_sent = True` in memory only; the
  commit happens afterwards in the caller. If the process dies between 11 and 12, the
  webhook has been delivered and the database still says unsent — the next scan re-sends
  it. There is no outbox and no idempotency key.
- **Step 6 is unbounded network work inside an open session.** For a large Shopify
  catalogue this holds a database connection for the entire scrape.
- **Step 8 is a large multi-row mutation with no explicit transaction boundary.** It
  relies on the caller's commit at step 9. A failure mid-detection rolls back to step 3.
- **Retries are configured but unreachable.** The task is declared
  `@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)` (`:19`), but the
  body catches every exception at `:133` and returns `{"status": "failed"}`. `self.retry()`
  is never called, so `max_retries` has no effect. Celery sees success every time.
- **Failure notification bypasses the event pipeline.** The `scrape_failed` Event is
  written at `:142`, then `notify_scrape_failure` is called directly at `:154` without
  ever setting `notification_sent` on that event. That event stays unsent forever and will
  be re-examined by every subsequent scan's step-10 query (it is skipped only because
  `dispatch_event_notifications` has no branch for `scrape_failed`).

### Initial-scan suppression

`tasks.py:110`: if this is the first scan for a competitor, or if more than 25
`new_product` events are pending, all `new_product` events are marked sent without being
delivered. This prevents a 500-message Discord flood on first scan. It is undocumented
outside the code and the threshold is a bare literal.

---

## 8. Detection and product identity

`services/detection.py:detect_changes` canonicalizes and deterministically collapses the
payload, then loads **all** products for the competitor into memory. It indexes them by
`canonical_url` and derived product-level `identity_key`, then matches each observation
canonical-URL-first, identity-key-second.

- Title-based matching was deliberately removed; the comment at `:42` explains why
  (stores reuse short item names). This is a good decision and should be preserved.
- Migration `0004` makes duplicate canonical URLs and non-null product identity keys
  impossible within one competitor. The explicit pre-migration audit/consolidator handles
  legacy conflicts without deleting snapshots or events.
- Any product not seen in a scan gets `consecutive_misses += 1`; at 3 it is deactivated
  and a `product_removed` event fires (`:201`). Because a failed scrape raises before
  reaching detection (`tasks.py:74`), a transient site outage does not falsely
  deactivate a catalogue — the empty-result guard is doing real work here.
- Price comparison (`:217`) uses an absolute epsilon of 0.001 and ignores the configured
  minimum-change thresholds entirely.
- The caller holds a transaction-scoped competitor advisory lock around the complete
  product read/decide/write region. A query against committed successful
  `(ScrapeRun.started_at, ScrapeRun.id)` values prevents an older-started acquisition from
  overwriting a newer-started successful observation. Failed runs do not establish
  ordering.

---

## 9. Scraping

Full detail in `docs/SCRAPING_ARCHITECTURE.md`. Summary of what exists:

`services/scraper.py:scrape_competitor` (`:51`) dispatches on `scrape_type`:

- `salla_json` → `scrape_salla_json` (`:284`)
- `shopify_json`, or `generic_selector` with no listing URLs → `scrape_shopify_json` (`:138`)
- otherwise → inline Playwright generic-selector scraping (`:67`–`:135`)

The Shopify path alone has **five** strategies tried in sequence: Storefront GraphQL (if
preferred), `/products.json` via aiohttp, the same via httpx with browser-ish headers,
per-collection targets, then a custom-storefront fallback that itself tries GraphQL and
then sitemap-crawls individual product pages parsing JSON-LD.

Notable: `_storefront_access_token_from_assets` (`:476`) downloads a competitor's
JavaScript bundles and regex-extracts their Shopify Storefront API access token, then uses
it to query their GraphQL API. See `docs/SECURITY.md` §4.

Domain vocabulary is hardcoded inside the scraper: `_shopify_product_category` (`:926`)
pattern-matches Roblox game names to assign categories, and `GENERIC_SHOPIFY_VENDORS`
(`:23`) lists competitor brand names to suppress as category values.

---

## 10. Notifications

`services/notification.py`. Discord webhooks only, via `aiohttp`, 10-second timeout, a
fixed 1-second sleep between messages (`:9`).

- `send_discord_webhook` (`:18`) returns `False` on failure. On HTTP 429 it sleeps for the
  advertised `retry_after` and then **returns False without retrying** — the message is
  dropped.
- No retry, no dead-letter, no backoff beyond that single sleep.
- The 1-second serial delay means a scan producing 100 events blocks for 100+ seconds
  inside the request or task.
- `dispatch_event_notifications` (`:188`) swallows per-event exceptions (`:261`) and
  leaves `notification_sent` false, so a failed event will be retried on the next scan of
  that competitor — eventually, and without bound.

---

## 11. Frontend

React 18 + Vite 5, TanStack Query, axios, react-router 6. Nine pages, one shared UI
module (`components/ui/index.tsx`), inline styles throughout, no CSS framework, no design
tokens.

- `src/lib/api.ts` is the single API access point — good — but every function is untyped:
  parameters are `any` and **no return type is declared anywhere**. Responses flow into
  components as `any`.
- `scanAllCompetitors` (`:14`) implements orchestration (worker pool, concurrency,
  per-item error capture, result aggregation) in the client. This is business logic in the
  transport layer.
- `SalesTrends.tsx` is the only page with real TypeScript types (`:9`–`:53`) — and it is
  also the only page that does **not** use TanStack Query, using `useEffect` +
  `useState` + manual loading/error flags instead. The two conventions are inconsistent.
- 47 occurrences of `any` across 10 files.
- Loading/error/empty states exist via shared `Loading`/`ErrorState`/`EmptyState`
  components and are used fairly consistently — this is better than typical.
- No frontend tests of any kind.

---

## 12. Health baseline — checks run at commit `f346f70`

Environment: macOS (darwin 25.5.0), Python 3.13.12 system / **3.10.4 in
`backend/.venv`**, Node 22.23.1, Docker available, no local PostgreSQL or Redis.

| Check | Command | Result |
|---|---|---|
| Backend unit tests | `.venv/bin/python -m pytest tests/ -q` | **65 passed**, 8 warnings |
| Backend import | `python -c "import app.main"` | **pass** (31 routes) |
| Alembic heads | `alembic heads` | `0002_product_category (head)` |
| Alembic upgrade | `alembic upgrade head` (temp PG 16) | **pass** |
| Alembic drift | `alembic revision --autogenerate` | **drift found** — see §4 |
| Frontend install | `npm ci` | **pass** |
| Frontend typecheck | `npx tsc --noEmit` | **FAIL** — 1 error, see below |
| Frontend build | `npm run build` | **pass** (687 KB single chunk) |
| Frontend lint | `npm run lint` | **BROKEN** — `eslint: command not found` |
| Frontend tests | — | **none exist** |
| Backend lint | — | **none configured** |
| Integration/E2E | — | **none exist** |

Pre-existing type error:

```
src/pages/SalesTrends.tsx(218,93): error TS2550: Property 'replaceAll' does not exist
on type 'string'. Do you need to change your target library? Try changing the 'lib'
compiler option to 'es2021' or later.
```

The build passes anyway because Vite transpiles with esbuild and never typechecks. This
error has been shippable and shipping.

`package.json` declares a `lint` script but eslint is not in `devDependencies`, so the
script has never been runnable as committed.

Python version mismatch: `pyproject.toml` requires `>=3.12`, the Dockerfile uses
`python:3.12-slim`, and the committed local virtualenv is 3.10.4.

---

## 13. Deployment topology

Two different topologies, with different behaviour.

**Docker Compose** (`docker-compose.yml`): postgres, redis, backend (runs
`alembic upgrade head` then uvicorn), celery_worker (concurrency 2), celery_beat, frontend
(nginx). This is the full architecture — queued scans work, beat scheduling works.

**Vercel** (`vercel.json`): `backend/main.py` as a serverless function under `/api`,
`maxDuration: 300`, plus the static frontend and a daily cron hitting `/api/cron/daily`.
**There is no Celery worker and no beat in this topology.** Consequently:

- Any scan that takes the `.delay()` path is queued to a broker with no consumer and never
  runs.
- Scheduled scanning depends entirely on the Vercel cron calling `/api/cron/daily`, which
  scans due competitors *sequentially and inline* (`api/cron.py:49`) within the 300-second
  function limit.
- `backend/main.py` is an ASGI wrapper that rewrites paths to add an `/api` prefix — a
  shim for Vercel's routing, and a source of divergence between local and deployed URL
  handling.

This is the single largest dev/prod configuration difference in the project, and it is not
documented anywhere in the repository outside this file.

---

## 14. Repository hygiene observed at baseline

- `.DS_Store` and `backend/.DS_Store` are **tracked** despite `.DS_Store` being in
  `.gitignore` (added to the index before the ignore rule).
- Six `__pycache__/*.pyc` files are tracked for the same reason.
- No `.env` file is tracked (correct). `.env.example` contains no real secrets (correct).
- `.vercel/` is gitignored and untracked (correct).
