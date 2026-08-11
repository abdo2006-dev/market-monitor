# Roadmap

Dependency-aware sequence. Each step is narrowly scoped and independently shippable.
**Do not batch steps** — the scan pathway is the highest-risk code in the repository and
large simultaneous changes there are not reviewable.

Current position: **Phase 1A complete. Phase 1B not started.** See `docs/PROJECT_STATUS.md`.

---

## Priority order

Reset in Phase 1A after the owner identified their daily workflows. Everything above the
line protects Search, Exports, and Sync — the three surfaces used every day to make real
pricing decisions (`docs/DAILY_CRITICAL_WORKFLOWS.md`).

| | Priority | Rationale |
|---|---|---|
| **P0** | Database / migration safety | Nothing else is safe to change until schema changes are safe. |
| **P1** | **Sync reliability** | Search and Export are only as trustworthy as the data Sync writes. |
| **P2** | Search reliability and freshness | Where pricing decisions actually get made. |
| **P3** | Export reliability and provenance | Stale data must never masquerade as live. |
| **P4** | Polished UX for Sync / Search / Export | Only once the data underneath is trustworthy. |
| — | Other Market Monitor functionality | Dashboard, Activity, Discord, Settings. |
| — | Treasury Audit | Design only. Blocked on P0–P3 and on unresolved legal questions. |

Do not spend effort below the line while anything above it is unreliable.

---

## Phase 0 — Baseline, audit, safety rails ✅ complete

Baseline archived, health measured, system reverse-engineered, 13 risks confirmed with
source references, V2 target designed, documentation suite written, minimal CI added.
One-line application change (`tsconfig.json` lib), verified byte-identical build output.

## Phase 1A — Daily critical path foundation ✅ complete

**P0 done.** Alembic is now the single schema authority: startup DDL removed, migration
`0003` converges the two historical index-naming schemes, a read-only Case A/B/C/D
diagnostic added, a manual-only production migration workflow added, and the CI drift
check promoted from `continue-on-error` to a required gate. Also fixed: `alembic revision`
had never worked (`alembic/script.py.mako` was missing from the repo).

**Regression coverage established.** 82 new tests across Sync (21), Search (23), Export
(28) and schema authority (10), against a real PostgreSQL database with deterministic
fixtures and no live network. 147 tests total.

**Characterised, not changed:** the concurrent-scan duplicate-product race (reproduced
deterministically), the export saved-data fallback, dead price-change thresholds, and the
Search freshness blind spot.

**Measured/documented:** freshness model (Part D), benchmark utility (Part E), topology
decision matrix (Part F), acquisition boundary sketch (Part G), critical-path TypeScript
contracts (Part H).

---

## Phase 1B — Make Sync reliable (P1)

**Objective: make competitor price synchronisation reliable enough that Search can be
trusted every morning.** Everything in this phase serves that sentence.

### 1B.0 — Run the benchmark and settle the topology · **blocking, not a coding task**

```bash
cd backend && .venv/bin/python scripts/benchmark_scan.py --json /tmp/bench.json
```

Record the numbers in `docs/DAILY_CRITICAL_WORKFLOWS.md` §6, then choose Option A, B, or C
and update `docs/adr/0006` to Accepted. Phase 1A recommends **Option A (persistent
worker)**, primarily because Option B's failure mode is silent.

**Nothing in 1B.2–1B.5 should start before this is settled** — the runner implementation
differs substantially between options.

### 1B.1 — Stop duplicate products (Y1 + Y2) · *can start immediately*

The highest-value correctness fix available, and independent of topology.

1. Audit existing duplicates and report before constraining.
2. Data-cleanup migration merging duplicate `(competitor_id, url)` rows, preserving
   snapshot history from both.
3. Add `UNIQUE (competitor_id, url)` and a partial unique on `external_id`.
4. Add a PostgreSQL advisory lock around scan entry.
5. Invert the assertion in `test_concurrent_scans_of_one_competitor_are_not_prevented`.

*Why it matters:* duplicates cause false `product_removed` events, which `sales-trends`
counts as phantom sales.

### 1B.2 — Durable scan lifecycle (ADR 0003)

`scrape_runs.trigger`, real status enum with non-terminal states, partial unique index,
`events.scrape_run_id`, `RequestCompetitorScan` / `ProcessCompetitorScan`, a reaper for
abandoned runs. Collapse all five pathways. Remove the private-worker imports from
`api/competitors.py` and `api/cron.py`.

### 1B.3 — Scoped, reliable notifications (ADR 0006 part 1)

Transactional outbox. Fixes duplicate sends, dropped 429s, and `scrape_failed` events that
are never marked notified (Y5).

### 1B.4 — Honour the detection policy (Y3, Y4)

Make `MIN_PRICE_CHANGE_AMOUNT` / `_PERCENTAGE` / `IGNORE_KEYWORDS` real, and stop treating
`in_stock → unknown` as a stock-out. Either wire `app_settings` into the policy or delete
the inert Settings page.

### 1B.5 — Move scan orchestration out of React

Delete `scanAllCompetitors` from `lib/api.ts`; add a bulk endpoint; poll run status.

---

## Phase 1C — Search freshness (P2)

Surface freshness in Search using the model in `docs/DAILY_CRITICAL_WORKFLOWS.md` §5.
Start with **absolute age** ("checked 3 hours ago") — do not invent a "stale" threshold
until 1B.0 has settled the real sync cadence. Add `scrape_runs.was_complete` for PARTIAL.

Also: `response_model` on the Search endpoints, then generated TypeScript contracts
(`docs/API_CONTRACTS.md` §5) replacing the hand-written `frontend/src/lib/types.ts`.

## Phase 1D — Export provenance (P3)

Implement the provenance contract in `docs/DAILY_CRITICAL_WORKFLOWS.md` §3.1:
`live | cached | partial | failed`, with headers for CSV/JSONL, a distinct filename for
non-live data, and UI confirmation before downloading cached data. Handle the unhandled
scraper exception (E3).

## Phase 1E — Acquisition boundary + scraper adapters (ADR 0004)

Capture fixtures first, then extract `ShopifyAdapter`, `SallaAdapter`,
`PlaywrightAdapter` behind `ProductObservation`, then `MarketDataAcquirer` so Sync and
Export share acquisition without sharing persistence
(`docs/DAILY_CRITICAL_WORKFLOWS.md` §8).

## Phase 2 — Correctness and honesty (below the critical-path line)

Broken or misleading rather than structurally wrong. None of this blocks P1–P3.
Items previously listed here that turned out to serve the daily workflows have moved
into Phase 1B (dead detection thresholds → 1B.4; duplicate-product constraints → 1B.1).

- **Make Settings actually work, or remove it.** `app_settings` is written and read by
  nothing; the UI is inert. If 1B.4 wires the detection thresholds in, the rest of the page
  still does nothing. Shipping a settings screen that does nothing is worse than not
  having one.
- **Resolve the remaining dead environment settings** — `DAILY_SUMMARY_*`,
  `DEFAULT_TIMEZONE`, `DEFAULT_CURRENCY` — or delete them from `config.py` and
  `.env.example`. Documenting inert settings as live is misleading.
- Fix `summary["biggest_drops"]`, hardcoded to `[]`.
- Fix `.env` loading (working-directory mismatch).
- Reconcile the Python version (venv 3.10.4 vs required ≥3.12).
- Decide on eslint: install it, or delete the `lint` script that has never been runnable.
- Security remediation in the order given in `docs/SECURITY.md` §4: fail-closed
  `CRON_SECRET`, CORS allowlist, mask webhook URLs, decide on Storefront token discovery,
  validate `base_url` on write, bound scraper response sizes.
- Structured logging with `scrape_run_id` correlation (A-12).

## Phase 3 — Remaining constraints and data quality

The product-identity constraints moved to **1B.1** — they protect Sync and cannot wait.
What remains:

- Check constraints on `event_type`, `scrape_type`, `stock_status`, `scrape_runs.status`.
- Composite index on `(competitor_id, status, started_at)` for eligibility queries.
- Move the Roblox taxonomy out of three source files into one owner (config or table),
  so adding a game stops requiring a deploy (`docs/DAILY_CRITICAL_WORKFLOWS.md` S4).
- Typed, validated `selector_config` per strategy, so a misconfiguration fails at the API
  instead of looking like a scraping failure.

## Phase 4 — Frontend structure

Feature-based reorganization (`features/competitors/`, …) — **after** typing, not before.
Component tests where logic exists. Code splitting for the 687 kB bundle. Playwright smoke
tests against a stubbed storefront. The UI is not being redesigned.

## Phase 5 — Treasury Audit (design only today)

Blocked on Phase 1 foundations **and** on unresolved legal/privacy questions
(`docs/design/TREASURY_AUDIT.md` §7) **and** on authentication existing at all
(`docs/SECURITY.md` §1.1). Do not begin implementation before those are settled.

---

## Not planned

Kubernetes · Kafka · service mesh · microservices · event sourcing as a storage pattern ·
CQRS read models · enterprise IAM · a UI redesign · replacing FastAPI, React, TanStack
Query, axios, or Celery.

Any of these needs its own ADR with a concrete triggering condition, not an assumption of
future scale. This is a single-operator application.
