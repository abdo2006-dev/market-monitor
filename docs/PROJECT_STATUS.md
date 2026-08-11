# Project Status

**Read this first.** This is the handoff file between working sessions. If it is stale,
fix it as part of the task.

_Last updated: 2026-08-12, Phase 1C Search verified locally._

## 1. Where we are

| | |
|---|---|
| **Current phase** | **Phase 1C Search complete locally** — ready for Phase 1D Export development; production merge, migration, and one-competitor Sync proof remain manual. |
| **Phase 1C base** | `c7c6f32ec6eaa6085be70ec80e6f0627ee33614c` |
| **Phase 1B.2 base** | `68db83e879a5ed738c80d0abddff10fa69f0dbb1` |
| **Working branch** | `v2/search-trust-ui` |
| **Migration head** | `0005_durable_sync_lifecycle` |
| **Archive baseline** | `archive/pre-v2-rearchitecture` → `f346f70`; do not move or delete. |
| **Production** | Not inspected, migrated, dispatched, or deployed by this phase. |

Priority remains: P0 migration safety, P1 Sync, P2 Search, P3 Export, P4 daily-workflow
UX, then lower-priority features. Treasury Audit remains design-only.

## 2. Phase 1C outcome

`/search` is now a trustworthy daily pricing surface rather than a numerically sorted
view of undifferentiated stored rows.

- The existing normalization, fuzzy scoring, collection/mutation identity, grouping, and
  representative thresholds remain regression-protected.
- Compare uses a guarded SQL fast path for definitive aliases, then the complete prior
  matcher for unresolved competitors. On a local 12-competitor/15,000-product exact-alias
  dataset, median compare latency fell from **967.83 ms to 91.40 ms**; PostgreSQL was not
  the bottleneck, so no extension/index/migration was added.
- Typed Search contracts expose product observation, producing run, latest complete,
  latest partial, last failure, current completeness, active Sync, and separate absolute
  product/coverage ages.
- Coverage states are `current_complete`, `partial`, `suspicious_empty`, `failed`,
  `stale`, and `unknown`. The expected cycle follows the 08:47 Cairo recovery boundary.
- A reliable market price must be current-complete, directly linked to the latest complete
  run, priced in an explicit currency, and confirmed in stock. Degraded observations stay
  visible as `lowest_observed_price` and never silently become the reliable reference.
- Lowest/highest/median reliable prices are calculated per currency. Missing or
  cross-currency values are never combined.
- Direct snapshots provide the latest real price-change context; events are not treated as
  price-history authority.
- The Search UI now has 250 ms autocomplete debounce, keyboard navigation, loading/error/
  empty/degraded states, responsive competitor cards, progressive evidence disclosure,
  and a durable Sync All action that never equates HTTP 202 with refreshed data.
- Vitest + Testing Library adds eight frontend behavior tests for the daily Search path.

Full flow, semantics, profile, query plans, UI ownership, and limitations:
`docs/SEARCH_ARCHITECTURE.md`.

## 3. Phase 1B.2 outcome

Sync now has one provider-neutral business lifecycle:

```text
manual / Sync All / Cairo morning trigger
              |
              v
Request*Scan application use case
              |
              v
PostgreSQL SyncRequest + queued ScrapeRun(s) -- optional server dispatch
              |
              v
worker claim (FOR UPDATE SKIP LOCKED + UUID fencing token + lease)
              |
              v
acquisition outside a DB transaction -> AcquisitionResult
              |
              v
competitor advisory lock -> freshness/completeness-aware reconciliation
              |
              v
terminal run, product state, snapshots, and events committed together
```

The API acknowledges durable requests with HTTP 202. It never reports request acceptance
or GitHub workflow dispatch as scan success. PostgreSQL owns requests, attempts, leases,
results, and lineage; GitHub Actions is only the initial execution provider.

### Durable model and state machine

- `sync_requests` groups one or more runs and records optional dispatch state.
- `sync_request_runs` permits a new request to reference an already-active competitor run.
- `scrape_runs` records queue, claim, acquisition, observation, reconciliation, retry,
  terminal, completeness, and safe failure metadata.
- Execution states are `queued`, `running`, `retry_wait`, `success`, `failed`, `abandoned`,
  and `stale_skipped`.
- Acquisition completeness is independent: `unknown`, `complete`, `partial`,
  `suspicious_empty`, or `failed`.
- A partial unique PostgreSQL index allows at most one non-terminal V2 run per competitor.
  Transaction advisory locks make request creation and reconciliation deterministic.
- Claims use `FOR UPDATE SKIP LOCKED`, commit before external I/O, and carry a UUID fencing
  token. The worker heartbeats the token's lease. A future worker recovers expired leases
  to `retry_wait`, or to `abandoned` after the attempt budget is exhausted.

### Completeness and observation correctness

- Complete, newer catalog coverage may infer absence and increment missing/removal state.
- Partial/truncated, suspicious-empty, and failed acquisitions never infer absence.
- Observed items in a partial result may still update when their external observation is
  newer.
- A full fifth Shopify page (`5 × 250 = 1,250`) is conservatively `partial`, because a
  sixth page may exist. Salla/generic adapters also signal a cap when pagination indicates
  more data.
- `Product.last_observed_at` and `last_observed_run_id` guard current state. Adapters stamp
  trusted server time at each page/batch or individual product-page boundary; acquisition
  completion is only a fallback. Run ID is the deterministic equal-time tie-break. An
  earlier observation that returns later cannot overwrite newer evidence.
- New snapshots and events carry nullable `scrape_run_id`; historical records are not
  assigned fabricated lineage.

### Entry-point convergence

| Original pathway | V2 disposition |
|---|---|
| `POST /api/competitors/{id}/scan-now` | Compatibility route delegates to `request_competitor_scan`; legacy inline/Celery code runs only when `SYNC_EXECUTION_MODE=legacy`. |
| `POST /api/competitors/scan-all` | Compatibility route delegates to one durable grouped request. |
| `GET /api/cron/scan-due` / `daily` | Creates/reuses the deterministic Cairo-day request and optionally dispatches it; daily summary remains legacy notification work. |
| Celery `scrape_competitor_task` / beat scheduler | Retained for rollback. V2 disables the every-minute legacy scan scheduler so both systems cannot execute one request. |
| React `scanAll()` fan-out | Removed. The browser makes one `/api/sync/all` request and polls durable status. |

There is one V2 reconciliation implementation (`application.sync` calling the existing
detection service). The legacy implementation remains behind the explicit rollback flag
until production proof is complete.

## 4. Execution providers

ADR 0008 is Accepted with this staged decision:

1. **Initial personal-use deployment:** GitHub Actions + PostgreSQL durable jobs.
2. **Professional reliability upgrade:** a persistent Railway service running the same
   `python -m app.workers.sync_worker` CLI and using the same database lifecycle.

`.github/workflows/sync-v2.yml` supports a safe request UUID for manual dispatch and two
daily recovery schedules at 07:17 and 08:47 `Africa/Cairo`. Both scheduled invocations derive the same
`automatic:<Africa/Cairo date>` request, so the second recovers rather than duplicates.
The workflow has no PR trigger, checks out trusted `main`, uses the `production-sync`
environment, pins third-party actions, and grants only `contents: read`.

GitHub enables manual dispatch and schedules only after the workflow exists on the default
branch. `production-sync` should restrict deployments to `main`, expose its database secret
only to the Sync job, and omit required reviewers for unattended morning execution unless
the owner explicitly chooses approval-gated runs.

The optional API dispatcher is disabled by default. When enabled, its fine-grained GitHub
token is server-only and should be limited to this repository with Actions write access.
A failed dispatch leaves the request durably queued; the next scheduled drain can claim it.

## 5. API and UI

New explicit contracts:

- `POST /api/sync/competitors/{id}` → 202 + request aggregate
- `POST /api/sync/all` → 202 + grouped request aggregate
- `GET /api/sync/requests/{uuid}`
- `GET /api/sync/runs/{id}`
- `GET /api/sync/freshness`

The Competitors page shows accepted, queued, running, retrying, success, partial, and
failed states; per-competitor run details; attempts; safe failures; completeness; observed
product counts; durations; and last complete coverage. It does not equate 202 with success.

Search now consumes run completeness/lineage and per-product `last_observed_at` directly,
returns typed trust and currency-market summaries, and renders them on the redesigned
daily-use page. Suggestions and compare have explicit response models. Batch Search and
the unused `/search/products` route retain their legacy contracts.

## 6. Production blockers and staged migration

Production may be unstamped or may not yet contain Phase 1B.1 constraints. Do not deploy
the worker or set strict schema mode until the owner performs `docs/RUNBOOK.md` §2.2–2.4:

1. read-only classification;
2. backup and documented restore plan;
3. exact Case B/B-/C handling (never stamp Case C);
4. duplicate audit/remediation if required;
5. upgrade through `0004`, then `0005`;
6. verify schema, indexes, constraints, and application startup;
7. merge the reviewed workflow to default `main` and configure `production-sync`;
8. deploy with the optional server dispatcher initially disabled;
9. run the documented one-competitor smoke (not Shopbloxs);
10. enable strict schema verification and optional dispatch only after proof.

Migration `0005` refuses to proceed while overlapping legacy `running` rows exist. Confirm
that no real worker owns them before resolving them. Its downgrade is implemented, but a
queued V2 row has no execution start; downgrade truthfully backfills legacy `started_at`
from `queued_at` before restoring the prior non-null column.

## 7. Verification status

All tests use deterministic fixtures/mocks; no live storefront or production database was
used.

| Gate | Result |
|---|---|
| Full backend | **213 passed**, 11 pre-existing warnings |
| Critical path | **142 passed**, 71 deselected |
| Phase 1C Search | **39 passed**: 35 PostgreSQL critical + 4 pure cycle-policy |
| Phase 1A daily/schema regression | **93 passed** |
| Phase 1B.1 Sync/integrity | **37 passed** |
| Phase 1B.2 lifecycle | **32 passed**, including 5 claim-race iterations |
| Observation-order selection | **4 passed** |
| Completeness safety selection | **5 passed** |
| Fresh / prior-`0004` upgrade | both reached `0005` head |
| Alembic drift | `No new upgrade operations detected` |
| Frontend tests | **8 passed** with Vitest + Testing Library |
| Frontend typecheck/build | pass; 2,413 modules, 704.45 kB main chunk |
| Startup/workflow | 36 routes with five Sync V2 routes; 2 YAML/security tests pass |
| Diff/secret safety | `git diff --check` and staged Gitleaks scan pass |

Known pre-existing warnings remain: Pydantic class-based config, FastAPI `on_event`, the
custom pytest-asyncio loop fixture, Starlette's multipart import, React Router v7 future
flags, Vite's CJS Node API, and the existing chunk-size warning. Frontend lint is not an
available gate because ESLint is not installed.

## 8. Remaining risks

- GitHub schedules are best-effort and can be delayed or disabled after repository
  inactivity. Manual runner startup also has queue/setup latency.
- A manual run that enters `retry_wait` may need a later manual or scheduled invocation;
  PostgreSQL preserves it, but GitHub Actions is not a persistent poller.
- Completeness depends on adapter evidence. The fifth-full-page rule is conservative, but
  a storefront that silently truncates without pagination evidence can still be misread.
- Notifications remain legacy and are not yet transactional/outbox-backed. Sync success is
  intentionally independent of Discord delivery.
- The old scraper remains a multi-platform service; Phase 1B.2 added a contract/telemetry
  boundary without performing the later adapter refactor.
- Production database classification/migration and real provider proof are still manual.
- Search suggestions still use an explicit 1,000-candidate cap; broad queries ask the user
  to add a word rather than claiming complete suggestion coverage.
- The market taxonomy remains hardcoded and duplicated with Export/scraper vocabulary.
- Search trust follows the accepted Cairo morning topology; a future promised cadence
  requires a policy/test update.
- Snapshot history records changes, not every observation, so Search shows recent change
  context rather than a dense price series.

## 9. Next recommended task

Begin **Phase 1D: make `/exports` truthful about live versus cached/partial data, reliable
for daily use, and visually polished.** Preserve Search and Sync contracts. Production
rollout remains independently gated by the merge/migration/smoke checklist in
`docs/RUNBOOK.md`; do not start Export work in the Phase 1C checkpoint.

## 10. Decisions not to reverse

1. Alembic is the only schema authority; startup verifies and never mutates schema.
2. PostgreSQL is the durable Sync coordination/source-of-truth layer; Redis is not.
3. Product identity is canonical URL plus derived product key, never title.
4. Absence inference requires complete, newer catalog coverage.
5. Actual external observation time orders product state; run start is not freshness.
6. Claims require a committed lease and fencing token; no transaction spans acquisition.
7. `DB_SCHEMA_CHECK=warn` remains until production is verified and migrated.
8. No live network calls in tests or CI and no secrets in logs, fixtures, or docs.
9. `archive/pre-v2-rearchitecture` must not be changed.
10. Preserve Git author identity and do not add AI attribution trailers.
11. Search never hides degraded observations or promotes them to the reliable market
    reference; currencies remain separate.

## 11. Local commands

```bash
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test \
  .venv/bin/python -m pytest tests/ -q
```

```bash
cd frontend
npx tsc --noEmit
npm run build
npm run test:run
```
