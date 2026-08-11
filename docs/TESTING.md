# Testing

> **Phase 1B.2 update.** Sections 1–3 below describe historical gaps at the Phase 0
> baseline. Phase 1A/1B.1 added daily-path and product-integrity coverage. Phase 1B.2 adds
> a real PostgreSQL lifecycle suite, so
> several "not covered" claims are now out of date. Current state: **§0** and
> `docs/DAILY_CRITICAL_WORKFLOWS.md` §9. The target pyramid in §4 is unchanged and still
> the plan.

---

## 0. Current state (Phase 1B.2)

The final Phase 1B.2 gate is **192 passed** against migrated PostgreSQL: 65 unit cases and
127 critical cases. The focused lifecycle suite contains 29 cases.

```bash
docker run -d --rm --name mm_pg -e POSTGRES_USER=market -e POSTGRES_PASSWORD=market -e POSTGRES_DB=market_monitor -p 5432:5432 postgres:16-alpine
```

```bash
docker exec mm_pg psql -U market -d postgres -c "CREATE DATABASE market_monitor_test;"
```

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test .venv/bin/python -m pytest tests/ -q
```

| Suite | Tests | Covers |
|---|---|---|
| `tests/test_core.py` | 65 | pure helpers (unchanged from Phase 0) |
| `tests/critical/test_schema_authority.py` | 10 | Alembic is the sole schema authority |
| `tests/critical/test_sync_regression.py` | 32 | reconciliation, idempotency, failure, concurrency, stale ordering |
| `tests/critical/test_product_integrity.py` | 5 | identity semantics, database constraints, duplicate audit/consolidation |
| `tests/critical/test_sync_lifecycle.py` | 29 | requests, idempotency, claims, leases, retries, completeness, freshness, lineage, API/worker truthfulness |
| `tests/critical/test_search_regression.py` | 23 | matching, grouping, best price, freshness blind spot |
| `tests/critical/test_export_regression.py` | 28 | validation, formats, fields, fallback provenance |

Harness: `tests/conftest.py`. Marker: `-m critical` / `-m "not critical"`.

**Database-backed tests skip when `TEST_DATABASE_URL` is unset.** That is correct locally
and unacceptable in CI, so CI sets it and additionally fails if a skip is detected. The
conftest refuses to run unless the database name contains `test`, so the suite cannot
truncate a real database.

The lifecycle tests specifically prove request→queue→claim→terminal transitions, one
concurrent claim owner, lease recovery/fencing, retry success/exhaustion, manual/Sync-All/
morning idempotency, the absence rules for complete/partial/capped/empty/failed results,
observation-time ordering, failed-later preservation, history lineage and replay safety,
HTTP 202 durability, dispatch failure truthfulness, and worker clean exit.

**Still missing**: frontend tests, backend lint/type checking, E2E, and
integration coverage for the Dashboard/Activity/notification paths. `npm run lint` remains
broken — eslint is declared in `package.json` but not installed.

---

## 1. What existed at the Phase 0 baseline

One file: `backend/tests/test_core.py`, 65 tests, all passing, all synchronous and pure.

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
# 65 passed, 8 warnings in 0.74s
```

| Class | Covers |
|---|---|
| `TestPriceParser` | currency symbols/codes, US and European decimal formats, edge cases |
| `TestTitleNormalizer` | unicode, punctuation, whitespace |
| `TestUrlNormalizer` | relative → absolute |
| `TestPriceEvents` | `_get_price_event_type` selection |
| `TestScraperParsing` | `_detect_stock` keywords |
| `TestNotificationRouting` | `get_notification_webhook_url` fallback |
| `TestShopifyScraper` | `_extract_shopify_product`, `_shopify_targets`, category inference |
| `TestSallaScraper` | `_extract_salla_product`, category-id parsing, URL localization |
| `TestCompetitorDefaults` | `_normalize_competitor_payload` |
| `TestScanSafety` | `_should_reject_empty_scrape` |
| `TestFuzzySearch` | match scoring, market identity, collection aliases |
| `TestCollectionExports` | export row building, filename, URL validation |

These are good tests of pure functions and should be kept as-is.

## 2. What is not covered

Everything that can lose or corrupt data.

- **No database tests at all.** No test database, no session fixture, no transaction
  rollback harness.
- **`detect_changes`** — the function that decides what is new, changed, and removed — has
  zero tests. Its helpers are tested; the function is not.
- **No API tests.** `TestClient` is never instantiated. No route has ever been exercised.
- **No worker tests.** The scan lifecycle, the three transaction boundaries, and the
  `is_initial_scan or backlog > 25` flood-control rule are untested.
- **No notification tests** beyond webhook-URL selection. The `notification_sent` state
  machine is untested.
- **No end-to-end scraper tests.** The five-strategy Shopify fallback chain — the source
  of at least four bug-fix commits — is untested.
- **No migration tests.** Nothing runs `alembic upgrade head` automatically.
- **No frontend tests.** No test runner installed.
- **No integration or E2E tests.**

Green currently does not mean safe. Every architectural change described in
`docs/ARCHITECTURE.md` can be made without any existing test noticing.

## 3. Other check gaps

| Check | Status |
|---|---|
| Backend lint / static analysis | **not configured** — no ruff, flake8, black, or mypy |
| Frontend lint | **broken** — `package.json` declares a `lint` script but eslint is not in `devDependencies`, so it has never run as committed |
| Frontend typecheck | **failing** — see below |
| Frontend tests | none |
| Coverage measurement | none |

Pre-existing type error, present at baseline:

```
src/pages/SalesTrends.tsx(218,93): error TS2550: Property 'replaceAll' does not exist
on type 'string'.
```

`npm run build` passes regardless, because Vite transpiles with esbuild and never
typechecks. Cause: `tsconfig.json` sets `lib: ["ES2020", ...]` while `String.replaceAll`
is ES2021. Every runtime this ships to (modern browsers, Node 22) supports it — the type
library was simply behind the code. Fixed in Phase 0 by adding `ES2021` to `lib`; see
`docs/PROJECT_STATUS.md`.

---

## 4. Target test pyramid

Sized for a single-operator application. The goal is to protect critical behaviour and
integration boundaries — **not** to reach a coverage percentage. Do not write tests to
raise a number.

```
        ╱────────────────╲     E2E (Playwright)          3–5 smoke paths
       ╱──────────────────╲    API + worker integration   ~25
      ╱────────────────────╲   DB integration             ~20
     ╱──────────────────────╲  Scraper contract           ~30 (fixtures)
    ╱────────────────────────╲ Domain / unit              ~120  ← 65 exist
```

### 4.1 Domain and unit — pure, fast, no I/O

Extend what exists. The high-value addition is `domain.reconcile()` once `detect_changes`
is lifted out of the database (see `docs/DOMAIN_MODEL.md` Part 2):

- new product → insert + snapshot + `new_product` event
- price up / down / to-null / from-null → correct event type and diff fields
- price change below the configured threshold → **no** event (currently impossible to
  test, and currently broken — thresholds are dead config)
- stock transitions in both directions
- unseen product: misses 1, 2, then deactivation + `product_removed` at 3
- a returning product reactivates and resets the miss counter
- URL match wins over `external_id` match
- duplicate URLs in one observation batch are collapsed deterministically

### 4.2 Database integration — real PostgreSQL

Run against a real PostgreSQL 16 container. Do not substitute SQLite: the application uses
JSON columns, `Numeric`, timezone-aware timestamps, partial indexes, and advisory locks,
none of which SQLite reproduces faithfully.

Pattern: session-scoped container, function-scoped transaction rolled back after each
test.

Cover: repository round-trips; cascade deletes (competitor → products/events/runs);
`SET NULL` on `events.product_id`; the uniqueness constraints once added; the partial
unique index preventing two non-terminal scrape runs; advisory-lock contention between two
sessions.

### 4.3 Migration checks

In CI, on a clean database:

1. `alembic upgrade head` succeeds.
2. `alembic downgrade base` then `upgrade head` succeeds (reversibility).
3. `alembic revision --autogenerate` produces an **empty** diff — models and migrations
   agree.

Check 3 fails today (`docs/ARCHITECTURE.md` A-1) and must be made to pass before it can be
enforced. Until then it runs as a reporting step, not a gate.

### 4.4 Scraper contract tests — deterministic fixtures only

Full design in `docs/SCRAPING_ARCHITECTURE.md` §2.5. The requirement that drives it: a
Shopify fix must not silently break Salla or generic scraping.

- One shared contract suite parameterized over every adapter, asserting the
  `ProductObservation` invariants.
- Per-adapter fixture directories under `tests/fixtures/scrapers/<adapter>/`.
- Shopify fallback-ordering tests (products.json 403 → httpx path → `strategy_used`).

**Absolute rule: no test may make a live network request.** The HTTP client is injected,
so fixtures are supplied by construction rather than by monkeypatching. This is a
correctness requirement, a speed requirement, and an acceptable-behaviour requirement —
CI must never scrape a third-party storefront.

Fixtures are captured from real responses once, trimmed to the fields the parser reads,
and **scrubbed of access tokens, cookies, and personal data** before committing.

### 4.5 Application / use-case tests

Test each use case against in-memory fakes for the ports, with a real database only where
the behaviour under test is a database behaviour.

- `RequestCompetitorScan`: creates a queued run; a second concurrent request returns the
  existing run rather than creating a duplicate; an inactive competitor is rejected.
- `ProcessCompetitorScan`: happy path commits products, snapshots, events, and outbox rows
  in one transaction; a transient adapter error marks `RETRYING` and re-enqueues; a
  permanent error marks `FAILED` and emits a `ScrapeFailed` event; **an empty result never
  deactivates products**; a redelivered message on an already-`RUNNING` run exits without
  double-processing.
- `DeliverNotifications`: claims a batch, sends once, marks delivered; a send failure
  increments attempts and backs off; a delivered entry is never re-sent.
- `ScanAllCompetitors`: only active competitors; competitors with a live run are skipped
  and reported as skipped.

### 4.6 API tests

`httpx.AsyncClient` against the ASGI app with a test database.

Per route: happy path shape, 404 on missing entities, 422 on invalid input, and a
**contract snapshot** — the serialized response compared against a committed JSON sample.
The snapshot tests are what make the "declare `response_model` on 14 routes" work
verifiable: add the model, assert the response is unchanged.

Plus: `/api/cron/*` returns 401 when `CRON_SECRET` is set and the header is wrong; the
exports SSRF guard rejects a foreign host.

### 4.7 Frontend

- **Typecheck** — `tsc --noEmit`, as a CI gate. Highest value per unit of effort here,
  since it is currently failing and unenforced.
- **Contract validation** — regenerate types from `openapi.json` and fail on diff
  (`docs/API_CONTRACTS.md` §4.3).
- **Component tests** — Vitest + Testing Library, only where logic exists: the competitor
  form's validation and payload construction, `lib/utils.ts` formatting, and the
  loading/error/empty branches of one representative page. Do not snapshot-test styled
  markup; these pages use inline styles and the snapshots would be pure churn.

### 4.8 End-to-end — 3 to 5 smoke paths, no more

Playwright against the Compose stack with a seeded database and a **stubbed competitor
storefront** served locally. Never against a real store.

1. Add a competitor → appears in the list.
2. Trigger a scan → run reaches a terminal state → products appear.
3. Change a stubbed price → rescan → a price-change event appears in Activity.
4. Market search → select an item → comparison table renders.
5. Collection export → file downloads with the expected header row.

These are slow and will be the flakiest thing in the suite; keep the count low and the
assertions coarse.

---

## 5. Rules

- **Never weaken a test to make it pass.** No deleted assertions, no loosened
  comparisons, no blanket `skip`, no `try/except` around a failing call. If a test fails,
  find the cause. If it is a real defect, report it.
- Every bug fix gets a regression test where one is reachable.
- No live network in any test, ever.
- No real credentials in fixtures — no webhook URLs, no access tokens.
- Deterministic time: inject a `Clock`; never assert against `datetime.now()`.
- If a check could not be run in your environment, say so explicitly. Do not report an
  unrun check as passing.

## 6. Commands

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test .venv/bin/python -m pytest tests/ -q
```

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test .venv/bin/python -m pytest tests/critical/test_sync_lifecycle.py -q
```

```bash
cd frontend && npx tsc --noEmit
```

```bash
cd frontend && npm run build
```

Migration verification (requires Docker):

```bash
docker run -d --rm --name mm_test_pg -e POSTGRES_USER=market -e POSTGRES_PASSWORD=market -e POSTGRES_DB=market_monitor -p 55432:5432 postgres:16-alpine
```

```bash
cd backend && DATABASE_URL="postgresql+asyncpg://market:market@localhost:55432/market_monitor" .venv/bin/python -m alembic upgrade head
```

Note: `backend/.venv` is Python 3.10.4 while `pyproject.toml` requires `>=3.12` and the
Dockerfile uses 3.12. CI runs 3.12. Reconciling the local virtualenv is a Phase 1 task.
