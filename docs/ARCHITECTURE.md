# Architecture

Two parts:

- **Part A — Phase 0 risk register**: the baseline findings, preserved as historical
  evidence. Some are resolved; `docs/PROJECT_STATUS.md` is authoritative for current state.
- **Part B — V2 architecture**: the target plus the implemented Phase 1B.2 Sync slice.

Part A baseline: commit `f346f70`, tag `archive/pre-v2-rearchitecture`.

---

# Part A — Architectural risk register

Every entry below was confirmed against source. Suspicions from the Phase 0 brief that
turned out **not** to hold are recorded in §A-14 so they are not re-investigated.

Severity scale: **Critical** (data loss, corruption, or production breakage waiting to
happen) · **High** (wrong behaviour under normal operation) · **Medium** (wrong behaviour
under load or edge conditions) · **Low** (maintainability, cost of change).

---

## A-1. Schema is defined twice and the definitions disagree — **Critical**

**Files** `backend/app/database.py:53-59` (`init_db`), `backend/app/main.py:42-44`
(startup hook), `backend/alembic/versions/*`

**Current responsibility** `init_db()` runs `Base.metadata.create_all` plus two raw
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements on every FastAPI startup. Alembic
separately owns the same schema.

**Why the coupling is dangerous** There is no single source of truth for the schema. The
ORM metadata and the migration scripts drift independently, and the application actively
applies the ORM version at boot.

**Concrete failure modes** — verified experimentally against PostgreSQL 16:

1. A database created by `init_db()` has **no `alembic_version` table**. The next
   `alembic upgrade head` runs `0001_initial` against a populated database and dies on
   `CREATE TABLE ... already exists`. Recovery requires a manual `alembic stamp`, and
   getting the stamp wrong silently skips migrations.
2. Index names differ by creation path: Alembic produces `ix_snapshots_product_id`,
   `create_all` produces `ix_product_snapshots_product_id`. A future migration that drops
   an index by name works on one deployment and fails on the other.
3. `docker-compose.yml:57` runs `alembic upgrade head` *before* uvicorn, so Compose
   happens to be safe. Vercel runs no migration step, so the deployed database is
   `create_all`-shaped and unstamped.
4. `create_all` never drops or alters. Any column removal or type change is silently
   ignored, so the running schema can lag the models indefinitely with no error.

**Recommended boundary** Alembic is the only writer of schema. Delete the DDL from
`init_db`; keep migrations in the deploy step. Reconcile index names in a migration that
uses `IF EXISTS`-tolerant renames.

**Migration risk** Medium — the existing production database must be inspected and
stamped correctly before the startup DDL is removed. Removing `init_db` DDL without
stamping first will leave an unmigratable database. This must be a runbook step, not just
a code change (see `docs/RUNBOOK.md`).

---

## A-2. Five scan execution pathways with divergent behaviour — **Critical**

**Files** `api/competitors.py:52` (scan-all), `api/competitors.py:152` (scan-now),
`api/cron.py:20` (cron scan-due), `workers/tasks.py:163` (beat scheduler),
`frontend/src/lib/api.ts:14` (client fan-out)

**Current responsibility** Each pathway independently decides eligibility, concurrency,
whether to run inline or via Celery, and what to return.

**Why the coupling is dangerous** "Scan a competitor" has no owner. A change to scan
policy — a new rate limit, a new guard, a new status — must be made in five places, and
nothing fails if you miss one.

**Concrete failure modes**

- `scan-now` (`:152`) performs no running-scan check at all, so the UI's per-row "Scan"
  button and the client-side "Scan All" can both start a scan on a competitor that beat
  is already scanning.
- `scan-now` returns `{"message","result"}` or `{"message","task_id"}` depending on
  `scrape_type`. Callers cannot tell which without inspecting the body.
- On Vercel there is no Celery consumer, so any competitor whose `scrape_type` is not
  `shopify_json`/`salla_json` takes the `.delay()` branch and **its scan silently never
  runs** — `task_id` is returned, the UI shows success, nothing happens.
- The backend `/scan-all` endpoint is unreachable from the UI (see A-3) yet remains a
  public route with its own divergent semantics.

**Recommended boundary** One `RequestCompetitorScan` use case, invoked by HTTP, bulk, and
scheduler alike. Inline execution becomes a deployment concern (a worker that runs
in-process), not a branch in a route handler.

**Migration risk** Medium — the Vercel topology genuinely cannot run a background worker,
so "always enqueue" must be paired with a deployment decision (see A-11 and
`docs/adr/0003-single-scan-pathway.md`).

---

## A-3. Scan orchestration lives in the browser — **High**

**Files** `frontend/src/lib/api.ts:14-56`, `frontend/src/pages/Competitors.tsx:36`

**Current responsibility** `scanAllCompetitors` filters competitors by `active`, spawns a
four-worker client-side pool, calls `scanNow` per competitor, captures per-item errors,
and aggregates a result summary. This is the code path the UI actually uses; commit
`3622524` moved it there deliberately.

**Why the coupling is dangerous** Business policy (who gets scanned, how many at once,
what counts as failure) is enforced by code the server does not control and cannot audit.

**Concrete failure modes**

- Closing the tab mid-run abandons every unscanned competitor with no record that a bulk
  scan was ever attempted.
- Two operators with two browser tabs produce eight concurrent scans, not four.
- The server cannot rate-limit outbound scraping, because it does not know a bulk
  operation is in progress.
- Bulk-scan results exist only in React state; nothing is persisted, so there is no
  history of bulk runs.

**Recommended boundary** The client sends one request expressing intent; the server owns
fan-out and returns identifiers the client can poll.

**Migration risk** Low — the UI change is small, and `Competitors.tsx` already ignores the
detailed result.

---

## A-4. API imports private worker internals — **High**

**Files** `api/competitors.py:95`, `api/competitors.py:162`, `api/cron.py:9`

```python
from app.workers.tasks import _scrape_competitor_async     # api/cron.py:9 (module level)
from app.workers.tasks import _scrape_competitor_async     # api/competitors.py:162 (inline)
```

**Current responsibility** Route handlers call an underscore-prefixed coroutine that owns
the entire scan lifecycle: transaction management, detection, notification dispatch.

**Why the coupling is dangerous** It inverts the dependency direction — transport depends
on worker implementation detail. The leading underscore is the author's own signal that
this is not a public interface, and it is being imported across a layer boundary anyway.
Any refactor of the worker breaks the API silently at import time.

**Concrete failure modes**

- `api/cron.py:9` imports at module scope, so importing the API package pulls in Celery
  and every worker dependency. This is why `app.main` cannot be imported without the
  Celery stack installed.
- The function was written assuming a Celery worker's lifecycle (its own event loop, its
  own process). Running it inside a FastAPI request means notification `asyncio.sleep(1)`
  calls block a request-handling task for the duration.
- `api/cron.py:65` calls `scan_due(...)` — a route handler — as an ordinary function from
  another route handler, passing the request-scoped `db` session through.

**Recommended boundary** Routes call application use cases. Celery tasks call the same use
cases. Neither imports the other.

**Migration risk** Low — this is a mechanical extraction once the use case exists.

---

## A-5. Notifications are not scoped to a scan run, and are sent outside the transaction — **High**

**Files** `workers/tasks.py:93-124`, `services/notification.py:188-262`,
`models/__init__.py:76` (`Event` has no `scrape_run_id`)

**Current responsibility** After a scan commits, the task selects *all* events for that
competitor with `notification_sent = false`, sends Discord webhooks, mutates the flags in
memory, and commits afterwards.

**Why the coupling is dangerous** Two independent defects compound:

1. **No run scoping.** The `events` table has no `scrape_run_id` column, so "events from
   this scan" is inexpressible. The query at `:93` is competitor-wide.
2. **Send-then-persist.** `dispatch_event_notifications` sets `notification_sent = True`
   on the ORM objects (`notification.py:259`); the commit happens at `tasks.py:124`.

**Concrete failure modes**

- Two concurrent scans of one competitor (possible per A-2): scan A commits its events,
  scan B's notification query picks up A's events as well as its own, and both scans send
  them. Duplicate Discord messages.
- A crash, deploy, or Celery `task_time_limit` (900s, `celery_app.py:23`) between the
  webhook POST and the commit re-sends every already-delivered message on the next scan.
- Serial `asyncio.sleep(1.0)` per message (`notification.py:70`) means 100 events take
  100+ seconds. On the Vercel inline path that is a third of the 300-second function
  budget spent sleeping, and a timeout there loses the flags entirely.
- `scrape_failed` events (`tasks.py:142`) are never marked sent because
  `dispatch_event_notifications` has no branch for them, so they accumulate forever with
  `notification_sent = false`, and are re-scanned by every subsequent notification query.

**Recommended boundary** Transactional outbox. Events and outbox rows are written in the
same transaction as the product changes; a separate worker claims outbox rows with
`FOR UPDATE SKIP LOCKED`, delivers with an idempotency key, and records the attempt.

**Migration risk** Medium — requires a migration (`scrape_run_id` on events, plus an
outbox table) and a new worker. Behaviour-preserving if the outbox worker starts before
the inline dispatch is removed.

---

## A-6. No idempotency and no locking; duplicate concurrent scans are possible — **High**

**Phase 1B.1 status:** Product reconciliation is resolved. A transaction-scoped PostgreSQL
advisory lock is acquired after acquisition and before the product read; it covers the
complete read/decide/write region. Older-started observations cannot overwrite a
later-started committed successful scan. The ordering watermark is the persisted
`(ScrapeRun.started_at, ScrapeRun.id)`, not the mutable competitor status; failed attempts
do not affect it. Durable request/run deduplication and the non-terminal
`ScrapeRun` state machine remain open for Phase 1B.2, so two overlapping attempts are
still recorded and both may perform network acquisition.

**Files** `api/competitors.py:171-182`, `api/cron.py:37-47`, `workers/tasks.py:189-199`

All three implement the same read-then-act check:

```python
cutoff = now - timedelta(minutes=30)
SELECT ScrapeRun WHERE competitor_id=? AND status='running' AND started_at > cutoff
```

**Why the coupling is dangerous** It is a textbook TOCTOU race with no lock, no unique
constraint, and no atomic state transition. Two callers read "no running scan"
microseconds apart and both proceed.

**Concrete failure modes**

- Duplicate scans → duplicate `Event` rows for the same price change → duplicate Discord
  notifications, inflated dashboard counts, inflated `sales-trends` stock-out counts.
- The 30-minute cutoff means a scan that hangs past 30 minutes stops blocking new scans
  but never transitions out of `running`, so `scrape_runs` accumulates permanent
  `running` rows that nothing reconciles.
- `scan-now` and the client fan-out skip the check entirely, so even a correct
  implementation of it would not protect the paths users actually press.

**Recommended boundary** A PostgreSQL advisory lock keyed on competitor id, taken inside
the use case, plus an explicit `ScrapeRun` state machine
(`QUEUED → RUNNING → SUCCEEDED|FAILED|ABANDONED`) with a partial unique index preventing
two non-terminal runs per competitor.

**Migration risk** Low-Medium — needs a migration for the index and a reaper for stale
`running` rows.

---

## A-7. Missing database constraints; product identity is enforced in Python only — **High**

**Phase 1B.1 status: Resolved for product identity.** Migration `0004` adds a unique
`(competitor_id, canonical_url)` constraint and partial unique
`(competitor_id, identity_key)` index. Raw Shopify `external_id` is not constrained
because its variant component is unstable; the derived key is product-level. Existing
duplicates require the explicit audited consolidator before the migration will proceed.
Free-text status/check constraints listed below remain future work.

**Files** `models/__init__.py:34-57`, `services/detection.py:225-246`,
`alembic/versions/0001_initial.py`

**Current responsibility** `products` has indexes on `competitor_id`, `url`,
`normalized_title`, `category`, `active` — and **no unique constraint at all**.
Uniqueness is enforced by `_index_existing_products` building `{p.url: p}` in memory.

**Why the coupling is dangerous** The database will happily accept duplicates the
application assumes cannot exist.

**Concrete failure modes**

- Two concurrent scans (A-6) both fail to find product X, both `INSERT`. Now two rows
  share a URL. `{p.url: p}` keeps one; the other is never matched again, accrues
  `consecutive_misses`, and after three scans emits a **false `product_removed` event** —
  which then feeds `sales-trends` as a phantom signal.
- `events.event_type` and `competitors.scrape_type` are unconstrained `String(50)`. A
  typo produces a row that no query matches and no error reports.
- `scrape_runs` has no index on `(competitor_id, status, started_at)` even though every
  eligibility check filters on exactly that triple.
- `app_settings` has no `CHECK (id = 1)` despite being a singleton by convention.

**Recommended boundary** `UNIQUE (competitor_id, url)` and a partial
`UNIQUE (competitor_id, external_id) WHERE external_id IS NOT NULL`; enum or check
constraints on `event_type`, `scrape_type`, `stock_status`, `scrape_runs.status`; a
composite index for the eligibility query.

**Migration risk** **High** — existing duplicates must be found and merged before a unique
constraint can be added. The migration needs a data-cleanup step, and merging must
preserve the snapshot history of both rows. This is Phase 1 work with a dry-run report,
not a one-line migration.

---

## A-8. A 1,195-line multi-platform scraper with no adapter boundary — **High**

**File** `backend/app/services/scraper.py`

**Current responsibility** One module implements: Playwright generic-selector scraping,
Shopify `/products.json` over aiohttp, the same over httpx with different headers, Shopify
Storefront GraphQL (including credential discovery), Shopify collection enumeration,
sitemap crawling with JSON-LD parsing, Salla category API scraping with locale/currency
handling, and Roblox-specific category inference.

**Why the coupling is dangerous** There is no interface, so there is no seam to test
against and no way to change one platform in isolation. `scrape_competitor` returns
`list[dict]` — an undeclared, unvalidated shape that every downstream consumer
re-guesses with `.get()`.

**Concrete failure modes**

- The Shopify path tries five strategies in sequence (`:156`, `:161`, `:172`, `:181`,
  `:363`). A change to the fallback ordering — which commits `f080dc0`, `4a6adea`,
  `8e5f78c`, `9660f6b` all touched — alters behaviour for Salla and generic scraping too,
  because they share the dispatcher and the helpers.
- Shared helpers (`normalize_url`, `parse_price`, `_regex_first`, `_title_from_handle`)
  are used by all platforms, so a fix for one silently changes the others. There is no
  test that would catch it: the current tests cover parsers and pure helpers, not the
  per-platform contract.
- The returned dicts are structurally optional everywhere. `detection.py:36` does
  `item.get("url", "")` and skips falsy URLs — a scraper regression that returns no URLs
  produces an *empty* scan rather than an error, which the `_should_reject_empty_scrape`
  guard then converts into a scan failure with a misleading message.
- Roblox domain vocabulary is embedded at `:23` (`GENERIC_SHOPIFY_VENDORS`, containing
  live competitor brand names) and `:942-951` (game-name pattern matching). Business
  taxonomy in an infrastructure module.

**Recommended boundary** A `ScraperAdapter` protocol returning a validated
`ProductObservation` list, one adapter per platform, fixtures per adapter. See
`docs/SCRAPING_ARCHITECTURE.md` and `docs/adr/0004-scraper-adapters.md`.

**Migration risk** Medium — the adapters can be extracted behind the existing
`scrape_competitor` signature one platform at a time, with fixtures captured from the
current implementation's real output first so the refactor is verifiable.

---

## A-9. Database, business, and notification concerns are mixed into worker tasks — **High**

**File** `workers/tasks.py:25-160`

**Current responsibility** A single 135-line function opens a session, manages three
transactions, calls the scraper, calls detection, updates two models, queries events,
builds lookup maps, applies a notification flood-control policy, dispatches webhooks, and
formats a response payload.

**Why the coupling is dangerous** None of it is independently testable. There is no way to
test the flood-control rule without a database, a scraper, and a Discord endpoint.

**Concrete failure modes**

- The `is_initial_scan or new_product_backlog > 25` policy (`:110`) is a business rule with
  a magic number, buried in a worker, with no test.
- All imports are function-local (`:26-33`) to dodge circular-import problems — a
  structural smell that hides the real dependency graph from tooling.
- The session is held open across the entire network scrape, so a slow competitor holds a
  PostgreSQL connection for minutes. With `NullPool` (`database.py:31`) each scan opens a
  fresh connection, so concurrent scans scale connections linearly with no ceiling.

**Recommended boundary** Worker = thin entrypoint. `ProcessCompetitorScan` use case owns
sequencing. Detection is pure. Persistence is a repository. Notification is an adapter
behind an outbox.

**Migration risk** Low — extraction is mechanical and the existing tests keep the pure
helpers honest.

---

## A-10. Weak typing across the Python/TypeScript boundary — **High**

**Files** `frontend/src/lib/api.ts` (all 20 exports), `backend/app/schemas/__init__.py:177`,
routes returning bare `dict`

**Current responsibility** The frontend has one centralized API module — good — in which
**no function declares a return type** and parameters are `any`.

**Why the coupling is dangerous** The compiler cannot see the contract, so a backend
response change is a runtime failure in a component, not a build error.

**Concrete failure modes**

- `api.ts:13 scanNow` returns two different shapes (A-2). `api.ts:24` reads
  `result.result?.status || (result.task_id ? 'queued' : 'completed')` — the ambiguity is
  handled by optional chaining rather than by types.
- 47 `any` occurrences across 10 files. `Dashboard.tsx:47`, `Products.tsx:79`,
  `Activity.tsx:108`, `MarketSearch.tsx:133` all map over `any` and index fields the
  compiler cannot verify.
- On the backend, `list_products`, `list_events`, `search_products`, `dashboard_summary`,
  `sales_trends`, `scan_now`, and `scan_all` return bare `dict`, so FastAPI's OpenAPI
  schema documents them as untyped objects. Contract generation cannot fix the frontend
  until these declare response models.
- `PaginatedResponse.items: List[Any]` (`schemas/__init__.py:177`) is declared but never
  used by any route.
- **The typecheck does not run and would fail if it did**: `npx tsc --noEmit` reports
  `SalesTrends.tsx(218,93) TS2550 replaceAll`, while `npm run build` passes because Vite
  never typechecks. A type error is currently shipping to production.

**Recommended boundary** Declare `response_model` on every route; generate TypeScript
types from the OpenAPI schema; make `tsc --noEmit` a CI gate. See
`docs/adr/0005-generated-api-contracts.md`.

**Migration risk** Low — additive. Declaring response models on existing routes is
verifiable route by route against recorded responses.

---

## A-11. Development and production configurations differ structurally — **High**

**Files** `docker-compose.yml`, `vercel.json`, `backend/main.py`

| | Docker Compose | Vercel |
|---|---|---|
| Celery worker | yes, concurrency 2 | **absent** |
| Celery beat | yes, 60s tick | **absent** |
| Migrations on deploy | `alembic upgrade head` | **absent** |
| Scheduling | beat, per-competitor frequency | HTTP cron, once daily |
| Scan execution | queued | inline, 300s limit |
| URL handling | direct | `main.py` ASGI path-rewrite shim |

**Concrete failure modes**

- Anything queued on Vercel is enqueued to a broker with no consumer. It never runs, and
  the API returns a `task_id` implying success.
- Vercel never runs migrations, so the deployed schema is whatever `create_all` produced
  (A-1).
- `backend/main.py:4-12` rewrites request paths to prepend `/api` for non-`/health`
  routes. Local development does not exercise this shim, so routing bugs are only
  reproducible in production.
- `celery_app.py` is imported by `workers/tasks.py`, which is imported at module scope by
  `api/cron.py` — so the serverless function loads Celery, Redis, and the beat schedule at
  cold start despite never using them.

**Recommended boundary** Pick one production topology and make the other a documented
development convenience. Either commit to a worker host (Compose/Fly/Render) or commit to
serverless and make the cron path the single authoritative scheduler with chunked,
resumable work.

**Migration risk** Medium — this is a deployment decision with cost implications, not
purely technical. Recorded as **unresolved** in `docs/adr/0006-background-jobs.md`.

---

## A-12. No observability and no log correlation — **Medium**

**Files** all `logger = logging.getLogger(__name__)` call sites; no logging configuration
exists anywhere in the backend.

**Concrete failure modes**

- No `logging.basicConfig`, no dictConfig, no formatter. Log output depends entirely on
  uvicorn's and Celery's defaults, which differ.
- Log lines carry no `scrape_run_id`, no `competitor_id` correlation field, no request id.
  Reconstructing "what happened during run 4213" means grepping timestamps across two
  processes.
- Mixed formatting styles — f-strings (`tasks.py:41`) and `%`-style (`scraper.py:214`) —
  so structured logging cannot be introduced by configuration alone.
- `ScrapeRun.error_message` is truncated to 1,000 characters with no traceback retained,
  so the durable record of a failure is a one-line string.
- No metrics of any kind: no scan duration, no products-per-scan trend, no notification
  success rate.

**Recommended boundary** Structured JSON logging configured once at startup, with
`scrape_run_id` and `competitor_id` bound as context for the whole scan.

**Migration risk** Low.

---

## A-13. Test coverage protects the safest code and none of the risky code — **Medium**

**File** `backend/tests/test_core.py` — 65 tests, all passing, all synchronous and pure.

Covered: price parsing, title/URL normalization, price event-type selection, Shopify and
Salla dict extraction, competitor defaults, `_should_reject_empty_scrape`, fuzzy search
scoring, collection-export helpers.

**Not covered at all:**

- Every database interaction. There is no test database, no fixture session, no
  integration test.
- `detect_changes` — the function that decides what is new, what changed, and what is
  removed.
- Every API route. No `TestClient` usage anywhere.
- The scan lifecycle in `workers/tasks.py`, including the flood-control rule.
- Notification dispatch and the `notification_sent` state machine.
- Per-platform scraper behaviour end to end. The Shopify tests exercise
  `_extract_shopify_product` on a dict; nothing tests the five-strategy fallback chain.
- Migrations. Nothing runs `alembic upgrade head` in CI.
- The entire frontend. No test runner is installed.

**Why this matters** The tests pass and will keep passing through every change described
in this document. Green does not currently mean safe.

**Recommended boundary** See `docs/TESTING.md`.

**Migration risk** None — additive.

---

## A-14. Suspicions from the Phase 0 brief that were **not** confirmed

Recorded so they are not re-investigated:

- **"Frontend duplicates backend business logic"** — mostly false. The frontend duplicates
  *orchestration* (A-3), not domain logic. Matching, scoring, and detection live only on
  the backend. `formatPrice`/`timeAgo` in `lib/utils.ts` are presentation helpers, which
  is correct.
- **"Missing loading/error/empty states"** — false. Shared `Loading`, `ErrorState`, and
  `EmptyState` components exist in `components/ui/index.tsx` and are used consistently
  across pages.
- **"Product identity matching is naive"** — false, and better than expected.
  `detection.py:42` explicitly documents why title matching was rejected in favour of URL
  and `external_id`. This decision should be preserved.
- **"Transaction boundaries are missing"** — partially false. They exist and are
  deliberate (`tasks.py:51`, `:90`, `:124`); the defect is *where* they are drawn relative
  to the network I/O and the notification send (A-5, A-9), not their absence.
- **"Exports endpoint is an SSRF hole"** — false. `_validate_collection_url`
  (`exports.py:203`) correctly requires scheme `http(s)` and host equality with the
  competitor's base URL.

Additional findings **not** anticipated by the brief:

- **`app_settings` is written but never read** (`docs/CURRENT_SYSTEM.md` §3). The entire
  Settings UI is inert. **High** — it is a user-facing feature that does nothing.
- **Nine environment settings are dead configuration** (`docs/CURRENT_SYSTEM.md` §5),
  including both price-change thresholds and `IGNORE_KEYWORDS`.
- **`.env` is never loaded by the backend** — `env_file=".env"` resolves relative to the
  working directory, and the only `.env` is at the repository root while the backend runs
  from `backend/`.
- **Celery `max_retries` is dead configuration** — the task body catches everything, so
  `self.retry()` is never reached (`tasks.py:19` vs `:133`).
- **`summary["biggest_drops"]` is hardcoded `[]`** (`tasks.py:248`), so the daily Discord
  summary always reports "None".
- **CORS is `allow_origins=["*"]` with `allow_credentials=True`** (`main.py:19-25`). See
  `docs/SECURITY.md`.
- **Cron endpoints are unauthenticated by default** — `CRON_SECRET` defaults to `None`
  and `_check_auth` is a no-op when unset (`api/cron.py:15`).

---

# Part B — V2 target architecture

## B-1. Shape

A **modular monolith** with background workers. One deployable application, one database,
explicit internal boundaries. Rationale and rejected alternatives:
`docs/adr/0001-modular-monolith.md`.

```
┌──────────────────────────────────────────────────────────────┐
│ api/            FastAPI routers. HTTP only.                  │
│   validation · auth · serialization · calling use cases      │
└───────────────────────────┬──────────────────────────────────┘
                            │ depends on
┌───────────────────────────▼──────────────────────────────────┐
│ application/    Use cases. Orchestration lives here.         │
│   RequestCompetitorScan · ProcessCompetitorScan              │
│   ScanAllCompetitors · UpdateCompetitor                      │
│   QueryMarketIntelligence · DeliverNotifications             │
└──────────┬────────────────────────────────┬──────────────────┘
           │ depends on                     │ depends on (interfaces only)
┌──────────▼─────────────────┐  ┌───────────▼──────────────────┐
│ domain/                    │  │ ports/  (Protocols)          │
│   Competitor · Product     │  │   CompetitorRepository       │
│   ProductObservation       │  │   ProductRepository          │
│   ScrapeRun · Event        │  │   ScrapeRunRepository        │
│   reconcile() · policies   │  │   ScraperAdapter             │
│   framework-free           │  │   Notifier · Clock · Outbox  │
└────────────────────────────┘  └───────────┬──────────────────┘
                                            │ implemented by
                    ┌───────────────────────▼──────────────────┐
                    │ infrastructure/                          │
                    │   postgres/  SQLAlchemy repositories     │
                    │   scrapers/  shopify · salla · playwright│
                    │   notifiers/ discord                     │
                    │   dispatch/  optional GitHub Actions     │
                    └──────────────────────────────────────────┘
                                            ▲
                    ┌───────────────────────┴──────────────────┐
                    │ workers/   provider-neutral CLI + legacy │
                    │   thin: claim → call application use case│
                    └──────────────────────────────────────────┘
```

**Dependency rule** Arrows point inward. `domain/` imports nothing from the other layers.
`application/` imports `domain/` and `ports/`, never `infrastructure/`. `api/` and
`workers/` are both adapters *into* `application/` and must not import each other.

This is a target, not a claim about today. See `docs/PROJECT_STATUS.md` for what is
actually built.

## B-2. Layer responsibilities

**api/** — Parse and validate requests. Enforce authentication where applicable. Call
exactly one use case. Serialize the result. Declare a `response_model` on every route.
No orchestration, no scraping, no direct repository access, no Celery imports.

**application/** — Each use case is a class or function with an explicit input, explicit
dependencies (injected ports), and an explicit result. Owns transaction boundaries,
locking, and the sequencing of domain and infrastructure calls. Knows nothing about HTTP
or Celery.

**domain/** — Entities and rules: what a price change is, when a product counts as
removed, whether an observation is admissible, what a scan run's legal state transitions
are. Pure functions and dataclasses. `reconcile(existing, observations) -> ChangeSet` is
the centrepiece — the current `detect_changes` logic with the database removed, which
makes it unit-testable for the first time.

**ports/** — `typing.Protocol` definitions only. No implementations.

**infrastructure/** — Everything that talks to the outside: SQLAlchemy repositories,
scraper adapters, the Discord notifier, the Celery queue. Each implements a port.

**workers/** — Provider adapters that deserialize safe identifiers, claim durable work,
and call application use cases. `sync_worker.py` is shared by GitHub Actions, Railway, and
local execution. Celery entrypoints remain only for rollback and notifications.

## B-3. Module map (target)

```
backend/app/
  api/                 competitors · products · events · search · dashboard
                       settings · exports · scans · cron
  application/         scans/    request_scan · process_scan · scan_all
                       catalog/  update_competitor · query_market_intelligence
                       notify/   deliver_notifications
  domain/              competitor.py · product.py · observation.py
                       scrape_run.py · events.py · reconcile.py · policies.py
  ports/               repositories.py · scraper.py · notifier.py · outbox.py
  infrastructure/      postgres/ (models, repositories, uow)
                       scrapers/ (shopify, salla, playwright, shared)
                       notifiers/ (discord)
                       queue/ (celery_app, tasks)
  workers/             entrypoints only
```

`api/search_dashboard_settings.py` splits: routes to `api/`, the identity and matching
engine to `domain/market_identity.py`, the Roblox vocabulary to configuration or a
database table (it is customer data, not code).

## B-4. Authoritative Sync lifecycle (implemented in Phase 1B.2)

See `docs/SCRAPING_ARCHITECTURE.md` §2.1 for the full state machine, idempotency,
completeness, locking, retry, lease, and transaction semantics. Summary:

```
HTTP | scheduler | bulk
        │
        ▼
RequestCompetitorScan
   ├─ pg_advisory_xact_lock(competitor_id)
   ├─ reuse a non-terminal run or INSERT queued run
   ├─ associate it with a durable SyncRequest             [TX]
   └─ optionally dispatch a provider after COMMIT
        │
        ▼
worker CLI → claim with FOR UPDATE SKIP LOCKED
   ├─ queued/retry_wait → running + lease + fencing token [TX, then COMMIT]
   ├─ acquire catalog → AcquisitionResult                  (no DB TX held)
   ├─ heartbeat lease while acquisition is active
   └─ advisory lock + freshness/completeness reconciliation
      + snapshots/events lineage + terminal state          [ONE TX]
```

The database partial unique index enforces one non-terminal V2 run per competitor. UUID
claim tokens fence an expired owner after lease recovery. `complete` coverage may infer
absence; `partial`, `suspicious_empty`, and `failed` cannot. Observation completion time,
then run ID, orders product state independently from request/start/commit timing.

There is no V2 behavioural difference between UI, scan-all, scheduler, or cron. Trigger is
diagnostic data, not a business-code selector. Notification delivery remains legacy and
does not participate in Sync success; the outbox is still future work.

Automatic morning entry points have one deployment-safety exception, not a domain-policy
fork: they require the default-off `SYNC_MORNING_ENABLED` rollout gate before they may
create or drain work. Manual request semantics remain identical. Production selects
GitHub as the single automatic owner and leaves the Vercel compatibility cron gated off.

## B-5. Phase 1C Search slice (implemented)

Interactive Search remains inside the modular monolith and PostgreSQL. The frontend owns
input behavior and presentation; the backend owns logical grouping, trust decisions,
currency comparability, and market statistics.

```text
React combobox -> typed Search routes -> SQL exact-alias fast path
                                      -> unresolved-competitor compatibility fallback
                                      -> existing Python market identity/matcher
                                      -> pure domain Search trust policy
                                      -> Sync/run/snapshot evidence
                                      -> typed market response
```

Phase 1C deliberately did not move the established matching engine out of the combined
route module. That refactor would create risk without improving the user's daily pricing
decision. Only the new trust policy is framework-free. Compare resolves definitive
score-1 aliases with a narrow query, then runs the complete prior matcher for unresolved
competitors. This removes the common full-table Python bottleneck without silently losing
fuzzy matches, a search service, or a schema change.

See `docs/SEARCH_ARCHITECTURE.md` for the contract, cycle policy, statistics, profile,
query-plan evidence, and remaining boundaries.

## B-6. Phase 1D Export slice (implemented)

Collection Export remains a synchronous HTTP download because its small, daily collection
workflow has no demonstrated need for a queue, object store, cache, or new durable export
record. The API owns acquisition/source choice and file truth; React only selects an
explicit mode, renders typed provenance, and asks the browser to download already prepared
bytes.

```text
Exports.tsx -> GET collection-prices (Axios blob)
  -> live: shared acquire_catalog -> AcquisitionResult -> file headers
  -> cached: active Product rows + ScrapeRun evidence -> file headers
```

`live` is the default and never consults stored rows. `cached` is a separate, explicit
stored-data request. The default CSV/JSONL row schemas and JSON envelope remain unchanged;
file-level `CollectionExportProvenance` uses response headers. The optional,
versioned `include_provenance=true` extension adds JSON metadata or extra row fields.
The shared Cairo market-cycle policy is owned by `domain.market_cycle`, and Export reuses
the Search catalog-coverage classifier rather than implementing another freshness clock.
See `docs/EXPORT_ARCHITECTURE.md` for the complete contract, security limits, and profile.

## B-7. What V2 explicitly does not include

No Kubernetes. No Kafka. No service mesh. No microservices. No event sourcing as a general
pattern — the outbox is a targeted delivery mechanism, not a storage model. No enterprise
IAM. No CQRS read models. This is a single-operator application; the goal is boundaries
that make change safe, not infrastructure that looks impressive.

Professional-scale alternatives, if the project ever needs them, belong in a separate ADR
with a concrete triggering condition.
