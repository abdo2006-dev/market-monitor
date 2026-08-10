# Project Status

**Read this first.** This is the handoff file between working sessions. If it is stale,
fix it as part of your task.

_Last updated: 2026-08-10, end of Phase 0._

---

## 1. Where we are

| | |
|---|---|
| **Current phase** | Phase 0 complete — audit, documentation, and safety rails. **Phase 1 not started.** |
| **Baseline commit** | `f346f7046b223b579c9ff3769f358c71e98f8989` ("Fallback collection exports to saved products") |
| **Baseline archive** | tag `archive/pre-v2-rearchitecture` → `f346f70`. **Do not move or delete.** |
| **Working branch** | `v2/architecture-foundation` (branched from `main` @ `f346f70`) |
| **`main`** | untouched at `f346f70` |
| **Application code changed in Phase 0** | one line — `frontend/tsconfig.json` (`lib: ES2020` → `ES2021`) |

Phase 0 deliberately did **not** rewrite the application. It produced an evidence-based
audit, a target architecture, and the minimum tooling to keep future changes honest.

---

## 2. What was completed

**Baseline preserved.** Archive tag and working branch created; `main` untouched.

**Health baseline measured.** Every available check run and recorded — see §4 and
`docs/CURRENT_SYSTEM.md` §12.

**System reverse-engineered from source.** All 26 API routes, 6 models, 2 migrations, 3
scraping platforms, 5 scan pathways, 9 frontend pages, and both deployment topologies
inventoried with file:line references.

**Architecture audit.** 13 confirmed risks with severity, failure modes, and migration
risk. Five of the brief's suspicions were **rejected** against source (recorded in
`docs/ARCHITECTURE.md` A-14 so they are not re-investigated), and seven findings the brief
did not anticipate were added.

**V2 target architecture designed** — modular monolith, one scan lifecycle, scraper
adapters, contract-safe API.

**Documentation created** (all reflect real source, none are placeholders):

```
AGENTS.md                          canonical agent instructions
docs/ARCHITECTURE.md               risk register + V2 target
docs/CURRENT_SYSTEM.md             as-built audit
docs/DATA_FLOW.md                  8 flows traced, current vs target
docs/DOMAIN_MODEL.md               persisted model + V2 domain
docs/SCRAPING_ARCHITECTURE.md      every strategy + adapter contract
docs/API_CONTRACTS.md              generated route table + contract defects
docs/TESTING.md                    coverage gaps + target pyramid
docs/DEPLOYMENT.md                 both topologies + env var reality
docs/SECURITY.md                   10 findings + remediation order
docs/RUNBOOK.md                    operational procedures
docs/ROADMAP.md                    phase sequence
docs/adr/0001..0006                six ADRs
docs/design/TREASURY_AUDIT.md      design only, not implemented
```

**CI safety rail added** — `.github/workflows/ci.yml`. Every step was verified locally
before being committed (see §4).

**Two Phase-0 code changes**, both justified below in §3.

---

## 3. Code changes made in Phase 0

### 3.1 `frontend/tsconfig.json` — `lib: ["ES2020", …]` → `["ES2021", …]`

**Why.** `npx tsc --noEmit` failed at baseline:

```
src/pages/SalesTrends.tsx(218,93): error TS2550: Property 'replaceAll' does not exist on type 'string'.
```

**Root cause, not a workaround.** `String.prototype.replaceAll` is ES2021; the tsconfig
declared an ES2020 type library. Every runtime this ships to (modern browsers, Node 22)
supports it. The *type library* was behind the code, not the other way round. Raising
`lib` — and not `target` — corrects the type environment without touching emit.

**Verified no behaviour change.** The production bundle is byte-identical before and after:
`dist/assets/index-CKtY0D7-.js`, 686.98 kB, same content hash. Vite/esbuild handles
transpilation independently of `tsconfig.lib`.

This was necessary because `tsc --noEmit` could not otherwise be made a CI gate.

### 3.2 Untracked `.DS_Store` and `__pycache__/*.pyc`

12 files removed from the Git index (`git rm --cached`), left on disk. All already matched
`.gitignore`; they had been committed before those rules existed. No source file was
touched.

---

## 4. Test and check status

All results from baseline `f346f70` unless noted. Environment: macOS, Node 22.23.1,
Docker available, no local PostgreSQL/Redis, `backend/.venv` on **Python 3.10.4**.

| Check | Command | Result |
|---|---|---|
| Backend unit tests | `cd backend && .venv/bin/python -m pytest tests/ -q` | **65 passed**, 8 warnings |
| Backend import | `python -c "from app.main import app"` | **pass**, 31 routes |
| Alembic heads | `alembic heads` | `0002_product_category (head)` |
| Alembic upgrade | `alembic upgrade head` (PG 16 container) | **pass** |
| Alembic round-trip | `upgrade head → downgrade base → upgrade head` | **pass** |
| Alembic drift | `alembic revision --autogenerate` | **DRIFT** — see §5.1 |
| Frontend install | `npm ci` | **pass** |
| Frontend typecheck | `npx tsc --noEmit` | baseline: **FAIL** · after §3.1: **pass** |
| Frontend build | `npm run build` | **pass**, 687 kB single chunk |
| Frontend lint | `npm run lint` | **BROKEN** — `eslint: command not found` |
| Frontend tests | — | **none exist** |
| Backend lint / typecheck | — | **none configured** |
| Integration / E2E | — | **none exist** |

**Pre-existing failures at baseline:** the frontend typecheck (fixed, §3.1), the broken
lint script (not fixed — see §6), and the Alembic drift (not fixed — needs ADR 0002).

**Not verified in this environment:** anything requiring a live Celery worker, Redis, or a
real Discord webhook. No live-site scraping was performed against competitor storefronts
beyond what the existing test suite does (which is none).

---

## 5. Known issues

Ordered by severity. Full analysis in `docs/ARCHITECTURE.md` Part A.

### 5.1 Critical

- **A-1 — Schema defined twice.** `database.py:53` runs `create_all` + raw `ALTER TABLE` at
  startup alongside Alembic. **Verified experimentally**: a `create_all`-built database has
  **no `alembic_version` table**, so `alembic upgrade head` against it will fail; and index
  names differ between the two paths (`ix_snapshots_*` vs `ix_product_snapshots_*`). The
  Vercel-deployed database is almost certainly in this state. Recovery procedure:
  `docs/RUNBOOK.md` §2.2.
- **A-2 — Five scan pathways** with divergent guards, response shapes, and execution
  models. On Vercel, queued scans are enqueued to a broker with no consumer and **silently
  never run** while the API returns a `task_id`.

### 5.2 High

- **A-3** Scan-all orchestration lives in the browser (`frontend/src/lib/api.ts:14`).
- **A-4** API modules import the private `_scrape_competitor_async` from the worker
  (`api/competitors.py:95`, `:162`; `api/cron.py:9`).
- **A-5** Notifications are unscoped (no `events.scrape_run_id`) and sent before the
  transaction commits → duplicate deliveries.
- **A-6** No locking; duplicate concurrent scans are possible. `scan-now` has no guard at all.
- **A-7** No unique constraint anywhere on `products`; identity is a Python dict.
- **A-8** 1,195-line multi-platform scraper with no adapter boundary or contract tests.
- **A-9** Worker tasks mix transactions, business rules, and webhook delivery.
- **A-10** 14 of 26 routes declare no `response_model`; no frontend function declares a
  return type.
- **A-11** Compose and Vercel differ structurally (worker, beat, migrations, Playwright).
- **`app_settings` is written but read by nothing** — the entire Settings UI is inert.
- **Nine environment settings are dead configuration**, including both price-change
  thresholds and `IGNORE_KEYWORDS`.
- **Cron endpoints are unauthenticated by default** (`CRON_SECRET` defaults to `None` and
  the guard is a no-op when unset).

### 5.3 Medium / informational

- A-12 no logging config, no correlation ids, no metrics.
- A-13 tests cover only pure helpers; nothing touching the database is tested.
- Celery `max_retries=3` is dead config — the task body catches everything, so
  `self.retry()` is never reached.
- `summary["biggest_drops"]` is hardcoded `[]`, so the daily Discord summary always says
  "None".
- CORS is `allow_origins=["*"]` with `allow_credentials=True`.
- `.env` at the repo root is **not loaded** by the backend (working-directory mismatch).
- `backend/.venv` is Python 3.10.4; `pyproject.toml` requires ≥3.12; Dockerfile uses 3.12.

---

## 6. Current blockers

**One blocking decision, and it gates Phase 1 scan work:**

> **Deployment topology must be chosen** — worker host (A), serverless (B), or hybrid (C).
> See `docs/adr/0006-background-jobs-and-delivery.md` §"Decision — part 2", which is
> deliberately left **Open**.
>
> This is not deferrable. ADR 0003's universal "create a durable run, then enqueue" design
> holds under all three options, but the *runner* differs enormously: Option B requires a
> chunked, resumable, cron-driven inline drain that Option A does not need at all.
> Building it before deciding means building the wrong one.
>
> Phase 0's recommendation is **Option A (worker host)** — every capability the code
> already assumes exists there for free, and Option B is silently dropping queued scans
> today. But this is a cost and preference call for the project owner.

**Non-blocking decisions that should be made soon:**

- eslint: install it or delete the broken `lint` script. Leaving a script that cannot run
  is the worst option.
- Shopify Storefront token discovery (`docs/SECURITY.md` §1.5): keep, restrict, or make
  opt-in. Currently on by default.

---

## 7. Pending migrations

**None.** Head is `0002_product_category` and both migrations apply and reverse cleanly.

Migrations Phase 1 will need — none written yet:

1. `events.scrape_run_id` (FK) — prerequisite for A-5.
2. `outbox_entries` table — ADR 0006.
3. `scrape_runs.trigger` + status enum + partial unique index on non-terminal runs — ADR 0003.
4. `UNIQUE (competitor_id, url)` + partial unique on `external_id` — **requires a data
   cleanup step first**; duplicates may already exist (A-7). Needs a dry-run report before
   the constraint is added.
5. Index-name reconciliation for the `create_all`/Alembic divergence (A-1).

---

## 8. Next recommended task

**Phase 1, Step 1: remove the dual schema definition (ADR 0002).**

Chosen first because it is the only Critical-severity item that is small, self-contained,
and blocks nothing else — and because every subsequent migration is unsafe until it is
done.

Sequence:

1. Inspect the production database: does `alembic_version` exist?
2. Back it up.
3. If unstamped, `alembic stamp 0002_product_category` (procedure: `docs/RUNBOOK.md` §2.2).
4. **Only then** remove `create_all` and the raw `ALTER TABLE` calls from
   `database.py:53-59`.
5. Add a migration step to the deployment process — Vercel currently has none.
6. Promote the CI drift check from `continue-on-error: true` to a required check.

**Files most relevant:**

- `backend/app/database.py:53-59` — the DDL to remove
- `backend/app/main.py:42-44` — the startup hook calling it
- `backend/alembic/versions/` — both migrations
- `docs/RUNBOOK.md` §2.2 — the recovery procedure
- `docs/adr/0002-postgres-source-of-truth.md` — the decision and its consequences
- `.github/workflows/ci.yml` — the drift step to promote

Do **not** start Phase 1 without confirming §6's topology decision if the task touches the
scan pathway. Step 1 above is safe to do regardless.

Full sequence: `docs/ROADMAP.md`.

---

## 9. Decisions that must not be unintentionally reversed

1. **Product identity matches on URL and `external_id`, never on title.**
   `detection.py:42` documents why — stores reuse short item names. Reintroducing title
   matching will silently merge distinct products.
2. **An empty scrape is treated as a failure, not an empty catalogue.**
   `_should_reject_empty_scrape` (`tasks.py:158`) is what prevents a transient site outage
   from deactivating an entire catalogue and firing hundreds of false `product_removed`
   events. Preserve it as an explicit domain rule.
3. **Initial-scan notification suppression.** First scan, or >25 pending `new_product`
   events → mark sent without delivering (`tasks.py:110`). Intentional flood control.
   Removing it will send hundreds of Discord messages on the next new competitor.
4. **The exports SSRF guard.** `_validate_collection_url` (`exports.py:203`) requires host
   equality with the competitor. Do not relax it.
5. **`archive/pre-v2-rearchitecture` must never be moved, force-updated, or deleted.**
6. **No live network requests in tests or CI**, ever. Scraper tests use committed fixtures.
7. **No AI co-author attribution in commits**; preserve the existing Git author identity
   (`Abdulrahman <abdalrahmanahmad2006@gmail.com>`).
8. **PostgreSQL is the source of truth.** Redis is a broker and cache only.
9. **Treasury Audit is design-only.** Do not implement it; it depends on Phase 1
   foundations and on unresolved legal/privacy questions
   (`docs/design/TREASURY_AUDIT.md` §7).
