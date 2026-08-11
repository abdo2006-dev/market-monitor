# Project Status

**Read this first.** This is the handoff file between working sessions. If it is stale,
fix it as part of your task.

_Last updated: 2026-08-11, Phase 1B.1 closeout and topology evidence gate._

---

## 1. Where we are

| | |
|---|---|
| **Current phase** | **Phase 1B.1 complete** — independently reviewed and checkpointed. ADR 0008 topology is Proposed; Phase 1B.2 not started. |
| **Phase 1B.1 baseline** | `27017abf17bd5f76447c6f0113e1988d49858d7b` |
| **Baseline archive** | tag `archive/pre-v2-rearchitecture` → `f346f70`. **Do not move or delete.** |
| **Phase 0 branch** | `v2/architecture-foundation` @ `07f780b` (unchanged) |
| **Working branch** | `v2/daily-critical-foundation`, branched from `07f780b` |
| **`main`** | untouched at `f346f70` |
| **Tests** | **163 passing** (65 unit + 98 critical-path) |

### Priority reset

The owner uses three workflows daily and makes real pricing decisions from them. As of
Phase 1A these are the highest-priority product surface — see
`docs/DAILY_CRITICAL_WORKFLOWS.md`:

**P0** database/migration safety · **P1** Sync reliability · **P2** Search reliability and
freshness · **P3** Export reliability and provenance · **P4** UX for those three ·
*then* everything else · *then* Treasury Audit.

---

## 2. What Phase 1A completed

### Phase 1B.1 — product integrity and concurrent Sync safety ✅

- Defined product identity in `app/domain/product_identity.py`: conservative canonical
  URL plus a derived product-level external key. Raw URL/external ID remain source data;
  titles never identify products.
- Added migration `0004_product_identity_integrity`: non-null canonical URL, partial
  product identity key, unique database invariants. It fails safely and names affected IDs
  if duplicate remediation has not happened.
- Added a read-only duplicate audit and explicit consolidator. Consolidation requires a
  backup acknowledgement, exact confirmation phrase, and recovery plan outside the repo;
  it repoints every snapshot/event before removing redundant current-state rows.
- Added a transaction-scoped PostgreSQL advisory lock after acquisition and before the
  product read. It covers the complete reconciliation transaction and never spans network
  or Playwright work.
- Added scan-start ordering protection based on committed successful `ScrapeRun.started_at`
  values: an older-started scan cannot overwrite or count misses after a later-started
  success commits, and failures cannot erase the successful watermark.
- Added deterministic payload deduplication and one observable retry for named product
  uniqueness collisions.
- Inverted the Phase 1A race regression. The unmodified defect reproduced 5/5 before the
  change; the fixed test now requires exactly one logical product.
- Added ADR 0007 and 16 regressions covering identity, locking, constraints, ordering,
  consolidation, and the duplicate → false-removal chain.

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
  Case A/A-/B/B-/C/D and never writes.
- Added `.github/workflows/db-migrate.yml` — **manual-only** production migration. Defaults
  to read-only `inspect`; write actions require typing `MIGRATE`; `stamp-head` refuses
  unless the inspector reports Case B.
- Added `backend/scripts/bootstrap_dev_db.sh` for the development path.

### Regression coverage ✅

98 critical-path tests, real PostgreSQL, deterministic fixtures, **no live network anywhere**:

| Suite | Tests |
|---|---|
| `tests/critical/test_schema_authority.py` | 10 |
| `tests/critical/test_sync_regression.py` | 32 |
| `tests/critical/test_product_integrity.py` | 5 |
| `tests/critical/test_search_regression.py` | 23 |
| `tests/critical/test_export_regression.py` | 28 |

Harness: `tests/conftest.py`. Database tests **skip** when `TEST_DATABASE_URL` is unset,
and CI fails if that happens there.

### Documented, not changed

Freshness model (Part D), acquisition boundary (Part G), critical-path contracts (Part H).
The real benchmark and refreshed topology matrix are now recorded in
`docs/DAILY_CRITICAL_WORKFLOWS.md` and proposed ADR 0008; no topology was implemented.

---

## 3. Code changes in Phase 1A

Phase 1B.1 adds:

| File/area | Change | Behaviour change? |
|---|---|---|
| `app/domain/product_identity.py` | canonical URL, product key, deterministic observation collapse | **Yes, intended** |
| `services/detection.py` | reconcile by canonical/product keys | **Yes, intended** |
| `workers/tasks.py` | transaction advisory lock, stale-start guard, named constraint retry | **Yes, intended** |
| `models` + migration `0004` | database product-identity invariants | schema + integrity |
| `scripts/audit_product_duplicates.py` | read-only production audit | no writes |
| `scripts/consolidate_product_duplicates.py` | explicit history-preserving remediation | only with confirmation |

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
| Full backend suite | `pytest tests/ -q` (with `TEST_DATABASE_URL`) | **163 passed** |
| Unit only | `pytest tests/ -q -m "not critical"` | 65 passed |
| Critical only | `pytest tests/ -q -m critical` | 98 passed |
| App import | `python -c "from app.main import app"` | pass, 31 routes |
| Fresh DB migration | `alembic upgrade head` on empty DB | pass |
| Round-trip | `upgrade → downgrade base → upgrade` | pass |
| **Drift** | `alembic revision --autogenerate` | **empty upgrade() — no drift** |
| Case A/A-/B/B-/C/D detection | `scripts/check_schema_state.py` | historical B- path added for safe `0003` stamping |
| Frontend typecheck | `npx tsc --noEmit` | pass |
| Frontend build | `npm run build` | pass, 686.98 kB |
| Frontend lint | `npm run lint` | **still broken** — eslint not installed |
| Frontend tests | — | still none |

**Not verified in this environment:** anything needing a live Celery worker, Redis, or a
real Discord webhook. One read-only real acquisition benchmark was run against configured
competitors; it performed no reconciliation or production writes (§6.2).

---

## 5. Known issues

Phase 0's A-numbered risks remain in `docs/ARCHITECTURE.md`. **A-1 and product-integrity
parts of A-6/A-7 are now fixed.**
Phase 1A additionally *proved* several with tests:

### Confirmed by reproduction

- **Y1/Y2 resolved.** The pre-fix duplicate reproduced deterministically 5/5. Reconciliation
  is now serialized, database uniqueness is final backstop, and the false-removal chain has
  a regression test.
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
A-5 (unscoped notifications), A-6 (durable request/run idempotency), A-7 (remaining
free-text status constraints), A-8 (monolithic scraper),
A-9, A-10, A-11 (topology), A-12 (observability). `app_settings` still inert. Cron
endpoints still unauthenticated by default.

---

## 6. Current blockers

**Two, both requiring the owner rather than an agent.**

### 6.1 Production database must be classified and stamped

Startup no longer creates schema. The deployed database may still be an unstamped Phase 1A
shape. The current classifier distinguishes **B-** (matches revision `0003`) from real
drift. Production must be classified, backed up, audited for duplicates, explicitly
remediated if necessary, and migrated to `0004` before strict mode:

- `DB_SCHEMA_CHECK=warn` keeps the app running and logs loudly. **Do not set `strict`
  before this is done** — it would refuse to boot.
- Migration `0004` must not be marked applied by stamping head.

Procedure: `docs/RUNBOOK.md` §2.2–2.3. Inspect → exact historical stamp if B- → back up →
read-only duplicate audit → explicit remediation if needed → migrate → verify → strict.
**Never stamp a Case C database.**

### 6.2 Deployment topology requires owner acceptance (ADR 0008)

The evidence gate is complete. The real sequential acquisition workload was 30.08 s wall
time for 12 active competitors (1.99 s median, 8.23 s slowest), with no configured browser
strategy and one empty result. ADR 0008 proposes:

1. primary: one persistent Railway Python worker polling PostgreSQL (~$5/month);
2. fallback: a public GitHub Actions ephemeral worker ($0, best-effort schedule/latency).

Live Export remains synchronous on Vercel. The owner must choose the cost/reliability
tradeoff before Phase 1B.2 starts. Celery/Redis migration, durable lifecycle, scan endpoint
changes, and worker deployment were deliberately not started.

---

## 7. Pending migrations

Head is **`0004_product_identity_integrity`**. Fresh upgrade and full round-trip are green.
Production has not been inspected or migrated — see §6.1.

Migrations later Phase 1B work still needs:

1. `scrape_runs.trigger`, status enum, partial unique index on non-terminal runs.
2. `events.scrape_run_id`, `product_snapshots.scrape_run_id`.
3. `outbox_entries`.
4. `scrape_runs.was_complete` for the PARTIAL freshness state.

---

## 8. Next recommended task

**Accept ADR 0008, then Phase 1B.2 — one reliable, observable, durable Sync lifecycle.**
Converge the five request paths on durable `ScrapeRun` request/process use cases, using the
selected runner. Do not begin the queue/worker migration until the owner chooses the
topology. Full sequence: `docs/ROADMAP.md`.

---

## 9. Decisions that must not be unintentionally reversed

1. **Product identity matches on canonical URL and derived product-level identity key,
   never on title.** Raw Shopify `external_id` includes a variant and is not itself the
   invariant. See ADR 0007.
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
