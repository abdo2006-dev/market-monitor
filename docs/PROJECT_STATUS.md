# Project Status

**Read this first.** This is the handoff file between working sessions. If it is stale,
fix it as part of your task.

_Last updated: 2026-08-11, end of Phase 1A._

---

## 1. Where we are

| | |
|---|---|
| **Current phase** | Phase 1A complete — database safety + daily critical path regression coverage. **Phase 1B not started.** |
| **Baseline commit** | `f346f7046b223b579c9ff3769f358c71e98f8989` |
| **Baseline archive** | tag `archive/pre-v2-rearchitecture` → `f346f70`. **Do not move or delete.** |
| **Phase 0 branch** | `v2/architecture-foundation` @ `07f780b` (unchanged) |
| **Working branch** | `v2/daily-critical-foundation`, branched from `07f780b` |
| **`main`** | untouched at `f346f70` |
| **Tests** | **147 passing** (65 pre-existing + 82 new critical-path) |

### Priority reset

The owner uses three workflows daily and makes real pricing decisions from them. As of
Phase 1A these are the highest-priority product surface — see
`docs/DAILY_CRITICAL_WORKFLOWS.md`:

**P0** database/migration safety · **P1** Sync reliability · **P2** Search reliability and
freshness · **P3** Export reliability and provenance · **P4** UX for those three ·
*then* everything else · *then* Treasury Audit.

---

## 2. What Phase 1A completed

### P0 — Alembic is now the single schema authority ✅

- Removed `Base.metadata.create_all` and the two raw `ALTER TABLE` statements from
  application startup (`app/database.py`). Startup now **verifies** the schema and never
  mutates it.
- Added migration `0003_reconcile_index_names`, which converges the two historical index
  naming schemes (`ix_snapshots_*` vs `ix_product_snapshots_*`). Written to be a safe no-op
  on either starting shape.
- **Fixed a repo defect: `alembic/script.py.mako` was missing**, so `alembic revision` had
  never worked — every migration to date was hand-written. Discovered when the CI drift
  check crashed rather than reporting.
- **CI drift check promoted from `continue-on-error` to a required gate.** `autogenerate`
  now produces an empty `upgrade()`, verified against both an Alembic-built and a
  `create_all`-built database.
- Added `backend/scripts/check_schema_state.py` — read-only, classifies a database as
  Case A/A-/B/C/D and never writes.
- Added `.github/workflows/db-migrate.yml` — **manual-only** production migration. Defaults
  to read-only `inspect`; write actions require typing `MIGRATE`; `stamp-head` refuses
  unless the inspector reports Case B.
- Added `backend/scripts/bootstrap_dev_db.sh` for the development path.

### Regression coverage ✅

82 new tests, real PostgreSQL, deterministic fixtures, **no live network anywhere**:

| Suite | Tests |
|---|---|
| `tests/critical/test_schema_authority.py` | 10 |
| `tests/critical/test_sync_regression.py` | 21 |
| `tests/critical/test_search_regression.py` | 23 |
| `tests/critical/test_export_regression.py` | 28 |

Harness: `tests/conftest.py`. Database tests **skip** when `TEST_DATABASE_URL` is unset,
and CI fails if that happens there.

### Documented, not changed

Freshness model (Part D), benchmark utility (Part E), topology decision matrix (Part F),
acquisition boundary (Part G), critical-path contracts (Part H).

---

## 3. Code changes in Phase 1A

| File | Change | Behaviour change? |
|---|---|---|
| `app/database.py` | `init_db()` → `verify_schema_state()`; no DDL | **Yes, intended** — startup no longer creates schema |
| `app/main.py` | startup calls verification; logs and re-raises `SchemaStateError` | Yes, intended |
| `app/config.py` | added `DB_SCHEMA_CHECK` (`strict`/`warn`/`off`, default `warn`) | additive |
| `alembic/versions/0003_*` | new migration, index-name convergence | schema only, non-destructive |
| `alembic/script.py.mako` | added missing template | fixes broken tooling |
| `pyproject.toml` | `[tool.pytest.ini_options]`: `asyncio_mode=auto`, `critical` marker | test-only |
| `frontend/src/lib/types.ts` | new, hand-written critical-path types | types only |
| `frontend/src/lib/api.ts` | typed 10 critical-path functions | types only |
| `MarketSearch.tsx`, `Exports.tsx`, `Competitors.tsx` | removed all `any`; fixed 2 latent nulls | 1 real guard added |
| `.github/workflows/ci.yml` | drift check now required; critical tests run; skip-detection | CI only |

**`DB_SCHEMA_CHECK` defaults to `warn` deliberately.** Removing startup `create_all` must
not take a running deployment down on boot. Flip to `strict` once production is verified
and stamped (§6).

---

## 4. Test and check status

Environment: macOS, `backend/.venv` Python 3.10.4, Node 22.23.1, Docker, PostgreSQL 16 in a
container. CI runs Python 3.12.

| Check | Command | Result |
|---|---|---|
| Full backend suite | `pytest tests/ -q` (with `TEST_DATABASE_URL`) | **147 passed** |
| Unit only | `pytest tests/ -q -m "not critical"` | 65 passed |
| Critical only | `pytest tests/ -q -m critical` | 82 passed |
| App import | `python -c "from app.main import app"` | pass, 31 routes |
| Fresh DB migration | `alembic upgrade head` on empty DB | pass |
| Round-trip | `upgrade → downgrade base → upgrade` | pass |
| **Drift** | `alembic revision --autogenerate` | **empty upgrade() — no drift** |
| Case A/B/C/D detection | `scripts/check_schema_state.py` | all four verified |
| Frontend typecheck | `npx tsc --noEmit` | pass |
| Frontend build | `npm run build` | pass, 686.98 kB |
| Frontend lint | `npm run lint` | **still broken** — eslint not installed |
| Frontend tests | — | still none |

**Not verified in this environment:** anything needing a live Celery worker, Redis, or a
real Discord webhook. The benchmark was exercised against an unreachable local host only —
**no real competitor site was scraped**, so there are no real duration numbers yet (§6).

---

## 5. Known issues

Phase 0's A-numbered risks remain in `docs/ARCHITECTURE.md`. **A-1 is now fixed.**
Phase 1A additionally *proved* several with tests:

### Confirmed by reproduction

- **Y1/Y2 — concurrent scans duplicate products.** Two overlapping scans of one competitor
  both INSERT; PostgreSQL accepts it (no `UNIQUE (competitor_id, url)`). Reproduced
  deterministically, 5/5 runs. The losing row later emits a **false `product_removed`**,
  which `sales-trends` counts as a phantom sale.
- **E1 — export cached data is indistinguishable from live data.** When the live scrape
  returns nothing, stored products are served with identical status, headers, filename and
  field set. The UI calls it a live scrape.
- **Y3 — price-change thresholds are dead config.** Detection uses a hardcoded 0.001
  epsilon; a 0.5-cent move on a $500 item fires an event.
- **Y4 — a degraded scraper manufactures sales signals.** `in_stock → unknown` emits
  `stock_out`, which is counted as an inferred sale.
- **S1 — Search has no freshness signal.** A month-old price outranks a minutes-old one
  purely by being lower.
- **Y5** `scrape_failed` events are never marked notified. **Y6** inactive competitor
  returns `{"result": null}` with a success message.
- **Contract bug** — `ScanAllItem.status` mixes two vocabularies; `'success'` leaks into a
  field whose consumers check `'completed'`. Works only by subtraction.

### Still open from Phase 0

A-2 (five scan pathways), A-3 (orchestration in React), A-4 (API imports worker internals),
A-5 (unscoped notifications), A-6/A-7 (locking, constraints), A-8 (monolithic scraper),
A-9, A-10, A-11 (topology), A-12 (observability). `app_settings` still inert. Cron
endpoints still unauthenticated by default.

---

## 6. Current blockers

**Two, both requiring the owner rather than an agent.**

### 6.1 Production database must be classified and stamped

Startup no longer creates schema. The deployed database is most likely **Case B**
(structurally correct, no `alembic_version` stamp). Until it is stamped:

- `DB_SCHEMA_CHECK=warn` keeps the app running and logs loudly. **Do not set `strict`
  before this is done** — it would refuse to boot.
- Migration `0003` cannot be applied.

Procedure: `docs/RUNBOOK.md` §2.2. Inspect → back up → stamp → verify → then `strict`.
**Never stamp a Case C database.**

### 6.2 Deployment topology still undecided (ADR 0006 part 2)

Blocks the Phase 1B scan migration. Requires benchmark numbers that do not exist yet:

```bash
cd backend && .venv/bin/python scripts/benchmark_scan.py --json /tmp/bench.json
```

Phase 1A's decision matrix recommends **Option A (persistent worker, ~$5–7/mo)**, mainly
because Option B's failure mode is silent: queued scans return a `task_id` and never run.
Record the measured numbers in `docs/DAILY_CRITICAL_WORKFLOWS.md` §6, then update ADR 0006
to Accepted.

---

## 7. Pending migrations

Head is **`0003_reconcile_index_names`**. All three apply and reverse cleanly on a fresh
database. `0003` is **not yet applied to production** — see §6.1.

Migrations Phase 1B will need (none written):

1. Duplicate-product cleanup, then `UNIQUE (competitor_id, url)` + partial unique on
   `external_id` — **requires a dry-run audit first**.
2. `scrape_runs.trigger`, status enum, partial unique index on non-terminal runs.
3. `events.scrape_run_id`, `product_snapshots.scrape_run_id`.
4. `outbox_entries`.
5. `scrape_runs.was_complete` for the PARTIAL freshness state.

---

## 8. Next recommended task

**Phase 1B.1 — stop duplicate products.** Independent of the topology decision, so it can
start immediately, and it is the highest-value correctness fix available.

1. Audit existing duplicates and **report before constraining**.
2. Cleanup migration merging duplicate `(competitor_id, url)` rows, preserving snapshot
   history from both.
3. Add `UNIQUE (competitor_id, url)` + partial unique on `external_id`.
4. Add a PostgreSQL advisory lock at scan entry.
5. **Invert the assertion** in
   `test_concurrent_scans_of_one_competitor_are_not_prevented` — it currently asserts
   `products == 2` and says in a comment that Phase 1B is done when it becomes `== 1`.

**Files most relevant:**

- `backend/tests/critical/test_sync_regression.py` — the race and constraint tests
- `backend/app/services/detection.py:225-246` — in-memory identity index
- `backend/app/workers/tasks.py:25` — the reconciliation body
- `backend/app/models/__init__.py:34` — `Product`
- `docs/DAILY_CRITICAL_WORKFLOWS.md` §4 — Y1/Y2
- `docs/RUNBOOK.md` §2.2 — required before any migration reaches production

Do **not** start 1B.2–1B.5 before §6.2 is settled. Full sequence: `docs/ROADMAP.md`.

---

## 9. Decisions that must not be unintentionally reversed

1. **Product identity matches on URL and `external_id`, never on title**
   (`detection.py:42`). Reintroducing title matching silently merges distinct products.
2. **An empty scrape is a failure, not an empty catalogue**
   (`_should_reject_empty_scrape`). This is what prevents a transient outage from wiping a
   catalogue. Guarded by `test_empty_scrape_is_a_failure_and_never_deactivates_products`.
3. **Initial-scan notification suppression** (`tasks.py:110`) — intentional flood control.
4. **The exports SSRF guard** (`exports.py:203`) — host must equal the competitor's.
5. **Alembic is the only schema authority.** No `create_all`, no runtime DDL. Guarded by
   `test_application_startup_never_creates_schema` (AST-based, so prose mentioning
   `create_all` does not trip it).
6. **`DB_SCHEMA_CHECK` default stays `warn`** until production is stamped (§6.1).
7. **`archive/pre-v2-rearchitecture` must never be moved, force-updated, or deleted.**
8. **No live network requests in tests or CI**, ever.
9. **No AI co-author attribution**; preserve the Git author identity
   (`Abdulrahman <abdalrahmanahmad2006@gmail.com>`).
10. **PostgreSQL is the source of truth.** Redis is a broker and cache only.
11. **Treasury Audit is design-only.**
12. **The Search matching algorithm was deliberately not rewritten.** 23 tests characterise
    it. Change it only with regression evidence.

---

## 10. How to run everything

```bash
docker run -d --rm --name mm_pg -e POSTGRES_USER=market -e POSTGRES_PASSWORD=market -e POSTGRES_DB=market_monitor -p 5432:5432 postgres:16-alpine
```

```bash
docker exec mm_pg psql -U market -d postgres -c "CREATE DATABASE market_monitor_test;"
```

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test .venv/bin/python -m pytest tests/ -q
```

```bash
cd frontend && npx tsc --noEmit && npm run build
```
