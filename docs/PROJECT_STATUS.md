# Project Status

**Read this first.** This is the handoff file between working sessions. If it is stale,
fix it as part of the task.

_Last updated: 2026-08-11, Phase 1B.2 implementation and local verification._

## 1. Where we are

| | |
|---|---|
| **Current phase** | **Phase 1B.2 complete locally** — durable, observable Sync lifecycle implemented; production migration and proof remain manual. |
| **Phase 1B.2 base** | `68db83e879a5ed738c80d0abddff10fa69f0dbb1` |
| **Working branch** | `v2/durable-sync-lifecycle` |
| **Migration head** | `0005_durable_sync_lifecycle` |
| **Archive baseline** | `archive/pre-v2-rearchitecture` → `f346f70`; do not move or delete. |
| **Production** | Not inspected, migrated, dispatched, or deployed by this phase. |

Priority remains: P0 migration safety, P1 Sync, P2 Search, P3 Export, P4 daily-workflow
UX, then lower-priority features. Treasury Audit remains design-only.

## 2. Phase 1B.2 outcome

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
- `Product.last_observed_at` and `last_observed_run_id` guard current state. Ordering uses
  actual acquisition completion time, then run ID as a deterministic equal-time tie-break.
  An older complete result that arrives later is `stale_skipped`.
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

## 3. Execution providers

ADR 0008 is Accepted with this staged decision:

1. **Initial personal-use deployment:** GitHub Actions + PostgreSQL durable jobs.
2. **Professional reliability upgrade:** a persistent Railway service running the same
   `python -m app.workers.sync_worker` CLI and using the same database lifecycle.

`.github/workflows/sync-v2.yml` supports a safe request UUID for manual dispatch and two
off-hour daily recovery schedules. Both scheduled invocations derive the same
`automatic:<Africa/Cairo date>` request, so the second recovers rather than duplicates.
The workflow has no PR trigger, checks out trusted `main`, uses the `production-sync`
environment, pins third-party actions, and grants only `contents: read`.

The optional API dispatcher is disabled by default. When enabled, its fine-grained GitHub
token is server-only and should be limited to this repository with Actions write access.
A failed dispatch leaves the request durably queued; the next scheduled drain can claim it.

## 4. API and UI

New explicit contracts:

- `POST /api/sync/competitors/{id}` → 202 + request aggregate
- `POST /api/sync/all` → 202 + grouped request aggregate
- `GET /api/sync/requests/{uuid}`
- `GET /api/sync/runs/{id}`
- `GET /api/sync/freshness`

The Competitors page shows accepted, queued, running, retrying, success, partial, and
failed states; per-competitor run details; attempts; safe failures; completeness; observed
product counts; durations; and last complete coverage. It does not equate 202 with success.

Phase 1C can consume `last_complete_at`, `latest_partial_at`, `last_failed_at`,
`coverage_complete`, active-run status, and per-product `last_observed_at`. Search itself
has not yet been redesigned or made freshness-aware.

## 5. Production blockers and staged migration

Production may be unstamped or may not yet contain Phase 1B.1 constraints. Do not deploy
the worker or set strict schema mode until the owner performs `docs/RUNBOOK.md` §2.2–2.4:

1. read-only classification;
2. backup and documented restore plan;
3. exact Case B/B-/C handling (never stamp Case C);
4. duplicate audit/remediation if required;
5. upgrade through `0004`, then `0005`;
6. verify schema, indexes, constraints, and application startup;
7. configure the `production-sync` environment and server dispatcher if desired;
8. enable strict schema verification and V2 only after an explicit test request succeeds.

Migration `0005` refuses to proceed while overlapping legacy `running` rows exist. Confirm
that no real worker owns them before resolving them. Its downgrade is implemented, but a
queued V2 row has no execution start; downgrade truthfully backfills legacy `started_at`
from `queued_at` before restoring the prior non-null column.

## 6. Verification status

All tests use deterministic fixtures/mocks; no live storefront or production database was
used.

| Gate | Result |
|---|---|
| Full backend | **192 passed**, 11 pre-existing warnings |
| Critical path | **127 passed**, 65 deselected |
| Phase 1B.1 Sync/integrity | **37 passed** |
| Phase 1B.2 lifecycle | **29 passed**, including 5 claim-race iterations |
| Fresh / prior-`0004` / downgrade-re-upgrade | all reached `0005` head |
| Alembic drift | empty generated upgrade/downgrade |
| Frontend typecheck/build | pass; 943 modules, 687.84 kB main chunk |
| Startup/workflow | 36 routes with Sync V2; YAML/security assertions pass |
| Diff/secret safety | pass before commit |

Known pre-existing warnings remain: Pydantic class-based config, FastAPI `on_event`, the
custom pytest-asyncio loop fixture, and Starlette's multipart import. Frontend lint is not
an available gate because ESLint is not installed; there are still no frontend unit tests.

## 7. Remaining risks

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

## 8. Next recommended task

After production migration and one explicit V2 proof, begin **Phase 1C: make `/search`
trustworthy, freshness-aware, fast, and polished for daily use.** Do not start it as part
of this phase.

## 9. Decisions not to reverse

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

## 10. Local commands

```bash
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test \
  .venv/bin/python -m pytest tests/ -q
```

```bash
cd frontend
npx tsc --noEmit
npm run build
```
