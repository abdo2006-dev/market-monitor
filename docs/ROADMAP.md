# Roadmap

Dependency-aware sequence from the Phase 0 audit. Each step is narrowly scoped and
independently shippable. **Do not batch steps** — the scan pathway is the highest-risk code
in the repository and large simultaneous changes there are not reviewable.

Current position: **Phase 0 complete. Phase 1 not started.** See `docs/PROJECT_STATUS.md`.

---

## Phase 0 — Baseline, audit, safety rails ✅ complete

Baseline archived, health measured, system reverse-engineered, 13 risks confirmed with
source references, V2 target designed, documentation suite written, minimal CI added.
One-line application change (`tsconfig.json` lib), verified byte-identical build output.

---

## Phase 1 — Foundations

Ordered by dependency. Steps 1.1–1.3 are independent of the topology decision; **1.4
onward requires it** (`docs/adr/0006` part 2 is deliberately Open).

### 1.1 — Single schema owner (ADR 0002) · **do this first**

*Why first:* the only Critical item that is small, self-contained, and blocks nothing —
and every later migration is unsafe until it is done.

1. Inspect production for `alembic_version`; back up; `alembic stamp` if unstamped
   (`docs/RUNBOOK.md` §2.2).
2. Remove `create_all` + raw `ALTER TABLE` from `database.py:53-59`.
3. Add a migration step to the Vercel deploy process (currently none).
4. Promote the CI drift check from `continue-on-error` to required.

*Risk:* Medium — the stamping step is irreversible if done wrong. Back up first.
*Verify:* CI drift check green; a fresh database boots only after migrations run.

### 1.2 — Contract typing, backend half (ADR 0005 steps 2)

Declare `response_model` on the 14 routes that lack one. Model shapes that already exist;
change nothing. Verify each by capturing the current JSON, adding the model, asserting the
response is unchanged.

*Risk:* Low, additive. *Independent of everything else — can run in parallel.*

### 1.3 — Contract typing, frontend half (ADR 0005 steps 3–5)

Add `openapi-typescript` (dev-only), generate `api-types.ts`, commit it, type all 20
functions in `lib/api.ts`, add the drift check to CI.

*Depends on:* 1.2. *Risk:* Low.

### 1.4 — Decide deployment topology (ADR 0006 part 2) · **blocking**

Not a coding task. Choose Option A (worker host), B (serverless), or C (hybrid).
Phase 0 recommends A. Record the outcome by updating ADR 0006 to Accepted.

**Nothing in 1.5–1.8 should start before this is settled.**

### 1.5 — Scraper adapters (ADR 0004)

1. **Capture fixtures from the current implementation first.** Prerequisite — without them
   the refactor is unverifiable.
2. Define `ProductObservation`, `ScrapeContext`, `ScrapeResult`, the protocol.
3. Extract `ShopifyAdapter`, keeping `scrape_competitor`'s signature as a shim.
4. Assert output matches the captured fixtures exactly.
5. Repeat for Salla, then Playwright.
6. Add the shared contract suite and Shopify fallback-ordering tests.

*Risk:* Medium. Highest-value step for future safety: after this, a Shopify fix cannot
silently break Salla or generic scraping.

### 1.6 — Durable scan lifecycle (ADR 0003)

Migrations: `scrape_runs.trigger`, status enum, partial unique index on non-terminal runs,
`events.scrape_run_id`. Then `RequestCompetitorScan` + `ProcessCompetitorScan`, advisory
locking, the state machine, and a reaper for abandoned runs.

Collapse all five pathways onto them. Remove the private-worker imports from
`api/competitors.py` and `api/cron.py`.

*Depends on:* 1.1, 1.4, and ideally 1.5. *Risk:* **High** — this is the core change.
*User-visible:* "Scan" becomes asynchronous and returns a run id to poll.

### 1.7 — Transactional outbox (ADR 0006 part 1)

`outbox_entries` table; write events + outbox in the same transaction as products; a
`DeliverNotifications` worker claiming with `FOR UPDATE SKIP LOCKED`; real backoff;
dead-lettering; 429 handled as a retry rather than a drop. Move `notification_sent` off
`Event`.

*Depends on:* 1.6 (needs `scrape_run_id`). *Risk:* Medium.
*Fixes:* duplicate notifications, dropped notifications, and failure alerts bypassing the
global enable switch.

### 1.8 — Move scan orchestration out of React (ADR 0003 / 0005 step 6)

Delete `scanAllCompetitors` from `lib/api.ts`; add a bulk endpoint; poll run status.

*Depends on:* 1.6. *Risk:* Low.

### 1.9 — Test foundations (`docs/TESTING.md`)

Database integration harness on a real PostgreSQL container; `reconcile()` unit tests; API
tests with contract snapshots; use-case tests. Migration checks are already in CI from 1.1.

*Interleave with 1.5–1.8 rather than doing it last* — each step should land with its tests.

---

## Phase 2 — Correctness and honesty

Things that are currently broken or misleading rather than structurally wrong.

- **Make Settings actually work, or remove it.** `app_settings` is written and read by
  nothing; the UI is inert. Either wire it into `DetectionPolicy` or delete the page.
  Shipping a settings screen that does nothing is worse than not having one.
- **Revive the nine dead environment settings** — `MIN_PRICE_CHANGE_*`, `IGNORE_KEYWORDS`,
  `DAILY_SUMMARY_*`, `DEFAULT_TIMEZONE`, `DEFAULT_CURRENCY` — or delete them from
  `config.py` and `.env.example`. Documenting inert settings as live is misleading.
- Fix `summary["biggest_drops"]`, hardcoded to `[]`.
- Fix `.env` loading (working-directory mismatch).
- Reconcile the Python version (venv 3.10.4 vs required ≥3.12).
- Security remediation in the order given in `docs/SECURITY.md` §4: fail-closed
  `CRON_SECRET`, CORS allowlist, mask webhook URLs, decide on Storefront token discovery,
  validate `base_url` on write, bound scraper response sizes.
- Structured logging with `scrape_run_id` correlation (A-12).

## Phase 3 — Constraints and data quality

- **Duplicate-product audit.** Report before constraining — duplicates may already exist.
- `UNIQUE (competitor_id, url)` + partial unique on `external_id` (A-7). Needs the cleanup
  migration.
- Check constraints on `event_type`, `scrape_type`, `stock_status`, `scrape_runs.status`.
- Composite index on `(competitor_id, status, started_at)` for eligibility queries.
- Move the Roblox taxonomy out of three source files into one owner (config or table).
- Typed, validated `selector_config` per strategy.

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
