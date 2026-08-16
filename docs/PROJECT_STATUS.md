# Project Status

**Read this first.** This is the handoff file between working sessions. If it is stale,
fix it as part of the task.

_Last updated: 2026-08-16, Phase 1G controlled Production release in progress; no Production write or deployment has occurred._

## 1. Where we are

| | |
|---|---|
| **Current phase** | **Phase 1G controlled Production release** — immutable source/ancestry checks and the complete local release suite pass. A minimal single-user access gate is implemented on the release branch because the current Vercel Hobby plan cannot protect Production domains. Production remains stopped at the provider-native recovery-object gate. |
| **Phase 1D base** | `e70de6d80e0ebeda5aeccf633e6d6f9c963d0fc7` (Phase 1C checkpoint) |
| **Phase 1C base** | `e70de6d80e0ebeda5aeccf633e6d6f9c963d0fc7` |
| **Phase 1B.2 base** | `68db83e879a5ed738c80d0abddff10fa69f0dbb1` |
| **Working branch** | `preview/market-monitor-v2` |
| **Migration head** | `0005_durable_sync_lifecycle` |
| **Archive baseline** | `archive/pre-v2-rearchitecture` → `f346f70`; do not move or delete. |
| **Production** | Current Vercel credential completed trusted-TLS read-only classification/audits. Schema is B-; 12 logical duplicate groups affect 24 products and 121 history rows (57 snapshots + 64 events). Nothing was migrated, consolidated, dispatched, deployed, merged, pushed, or reconfigured. |
| **User-test Preview** | Vercel Preview is Ready on `preview/market-monitor-v2`, backed by isolated resource `market-monitor-v2-preview-db` at migration head with seven deterministic competitors and no runner. See `docs/PREVIEW_TESTING.md`. |

Priority remains: P0 migration safety, P1 Sync, P2 Search, P3 Export, P4 daily-workflow
UX, then lower-priority features. Treasury Audit remains design-only.

The Phase 1G owner authorization permits the bounded release runbook only after every hard
gate passes. No Production database/configuration write, merge, or deployment has occurred
yet. The deterministic validation, one permitted low-impact 12-store coverage run, and
approved-credential read-only Production classification/audits are complete. The next step
is to establish and independently read a Neon recovery object before any schema/data write.

## 1.1 Phase 1G access-control gate (in progress)

- Vercel API evidence identifies the project plan as Hobby. Current Vercel documentation
  states Standard Protection excludes Production domains on that plan, so provider-level
  protection cannot satisfy this release without a paid plan change.
- `app.auth` therefore supplies the brief's application-level fallback: one PBKDF2 password
  hash, independent HMAC session secret, expiring Secure/HttpOnly/SameSite=Strict cookie,
  same-origin mutation checks, and no browser-visible infrastructure credential.
- All data/mutation APIs are denied before routing when unauthenticated. Auth status/login
  and health are the only public application paths; cron retains a distinct bearer and now
  fails closed when its secret is absent.
- The SPA renders only the login gate until session proof succeeds and exposes an explicit
  lock action. Cross-origin API access is disabled.
- The local release suite passes: 242 backend tests against disposable PostgreSQL, 19
  frontend component tests, 15 Playwright cases (plus six intentional viewport skips),
  TypeScript, production build, full downgrade/upgrade sequencing, Alembic drift, and
  strict application import with 39 routes. Exact-checkpoint CI and the final staged secret
  scan remain release gates before merge/deploy.

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

## 2.1 Phase 1D outcome

`/exports` is now an honest daily collection-download workflow.

- Live is the default and calls the shared `acquire_catalog()` boundary. It returns only
  observations from this request, with complete/partial/suspicious-empty evidence. It never
  substitutes `Product` rows.
- Live acquisition failure returns a safe structured 502. Cached data is a separately
  selected mode; unavailable cached data returns a safe structured 404.
- Default CSV, JSONL, JSON envelope, filenames, title sort, and browser download behavior
  remain compatible. `X-Market-Monitor-Export-*` headers carry typed source, completeness,
  cap, page/count, time, coverage, and lineage evidence. `include_provenance=true` is the
  explicit additive payload extension.
- Cached files disclose row observation range, real latest complete/terminal run IDs, shared
  Cairo-cycle coverage state, and legacy/mixed-lineage counts. Durable collection membership
  does not yet exist, so cached collection reconstruction remains alias-based and disclosed.
- The responsive Exports page prepares a Blob before download, shows loading, complete,
  partial, suspicious-empty, stored, failure, and no-cache states, then requires explicit
  download confirmation. A mobile browser check found and fixed a sidebar-width issue.
- No schema/model migration was needed. `domain.market_cycle` is the one owner of the Cairo
  recovery boundary, and Export reuses the Search catalog-coverage policy.

The local 1,250-row CSV+JSON serialization profile compared the Phase 1C export helper to
Phase 1D over 20 samples of 20 repetitions: median **7.35 ms → 7.34 ms**, p95 **7.40 ms →
7.43 ms**. This excludes provider/network acquisition, which remains the dominant and
already bounded request cost; no queue/cache/object-store/export-run infrastructure is
justified by this evidence. Full details: `docs/EXPORT_ARCHITECTURE.md`.

## 2.2 Premium daily-workflow UX outcome

Search, Exports, and Competitors now use one documented visual and interaction system; see
`docs/UI_UX_SYSTEM.md`.

- The application shell uses a restrained graphite/blue palette, Lucide icons, grouped
  navigation, a clear protected-Preview state, and route-level lazy loading. Below 960 px it
  becomes a mobile header, three-workflow bottom navigation, and secondary navigation drawer.
- Search is a denser decision surface. Reliable market references remain visually primary,
  while older/degraded evidence is grouped separately without hiding it or changing backend
  trust calculations.
- Exports is an explicit four-step prepare/review/download workflow. Live, stored, partial,
  suspicious-empty, failure, and no-cache outcomes remain distinct.
- Competitors is an operations center with readiness counts, lifecycle/completeness evidence,
  primary Sync actions, and secondary destructive actions moved into contextual menus.
- Shared primitives now cover fields, buttons, tables, feedback, stock indicators, focus,
  loading, and reduced motion. The browser audit covers 1440, 1280, 1024, 961/959, 768, and
  390 px without horizontal document overflow or console errors.
- No API contract, data model, migration, Sync ownership rule, Search trust rule, Export
  provenance rule, fixture safety gate, or Production setting changed.

## 2.3 Phase 1F acceptance and coverage outcome

Phase 1F adds repeatable release evidence without turning third-party availability into a
flaky required test.

- `backend/tests/acceptance/` now covers long Shopify catalogs (including 1,767 products),
  exact/empty terminal pages, the 100-page hard ceiling, duplicate/malformed/mid-failure
  safety, root-products Storefront GraphQL, sitemap, Salla cursor, generic observations,
  identity/price/currency/stock invariants, sanitized failures, and the exact registry.
- A migrated-PostgreSQL morning journey proves durable Sync → freshness-aware Search →
  explicit cached Export → independently acquired live Export. HTTP 202 remains queued,
  never completed.
- Playwright drives the actual React app at 1440×900, 1024×768, and 390×844 with synthetic
  API interception. It parses real CSV/JSON/JSONL downloads, checks provenance/failure
  states, keyboard/Escape/focus/reduced-motion behavior, overflow, and console errors.
- Required CI runs deterministic acceptance, component tests, Playwright, typecheck, build,
  critical PostgreSQL tests, migrations, and drift. Live storefront calls are isolated in
  a manual, read-only, database-free, non-blocking workflow with pinned actions and a
  sanitized short-retention artifact.
- The default adapter page ceiling is now 100; providers still stop at a real end signal.
  Full terminal pages, non-advancing cursors, repeated pages, malformed responses, and
  failures remain partial/failed evidence and never authorize absence inference.
- A platform-level Vite/minified-Shopify discovery pattern enabled Shopbloxs root-products
  GraphQL without hardcoding or logging a Storefront token. Browser acceptance also found
  and fixed the Export filename parser, which previously ignored `Content-Disposition`.

Live evidence on 2026-08-16 is **10 healthy / 2 failed**. The healthy set includes
Shopbloxs (534 products via root GraphQL) and BloxCrew (1,067 via GraphQL); across the ten
healthy stores, 9,376 products had 100% valid-price coverage and zero duplicate identity/
canonical-URL evidence, with no observed 429 or 5xx response. TubbysTubby's owner-provided
canonical host is a parked/non-catalog site and fails safely. BuyBlox fails as
`temporary_network`, consistent with its current certificate-chain problem; TLS validation
was not bypassed. See `docs/COMPETITOR_COVERAGE.md`.

Dependency review reduced npm audit from 13 advisories (1 critical, 5 high) to 4 (1 low,
3 moderate, zero critical/high) using compatible explicit upgrades. The remaining React
Router advisories require a deliberate v7 migration and concern redirect/SSR surfaces not
used by this fixed-route client-only SPA.

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
- A full final Shopify page at the configured ceiling is conservatively `partial`, because
  another page may exist. The historical five-page example was `5 × 250 = 1,250`;
  Phase 1F raises the default safety ceiling to 100. Salla/generic adapters also signal a
  cap when pagination indicates more data.
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
The schedule job, worker morning mode, and Vercel compatibility cron are guarded by
default-off `SYNC_MORNING_ENABLED`; GitHub alone is enabled after manual proof.
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
| Full backend | **237 passed**, 11 pre-existing warnings, against disposable local PostgreSQL |
| Critical path | **143 passed**, 78 deselected, against disposable local PostgreSQL |
| Phase 1C Search | **39 passed**: 35 PostgreSQL critical + 4 pure cycle-policy |
| Phase 1D Export + cycle policy | **28 passed**: 24 PostgreSQL critical + 4 pure policy |
| Phase 1A daily/schema regression | **93 passed** |
| Phase 1B.1 Sync/integrity | **37 passed** |
| Phase 1B.2 lifecycle | **34 passed**, including 5 claim-race iterations and two default-off morning gates |
| Observation-order selection | **4 passed** |
| Completeness safety selection | **5 passed** |
| Fresh / prior-`0004` upgrade | both reached `0005` head |
| Alembic drift | `No new upgrade operations detected` |
| Preview/security release gate | **11 passed**, including seed schema/content ownership preflight |
| Frontend tests | **19 passed** with Vitest + Testing Library (9 Search + 8 Export + 2 Competitors) |
| Frontend typecheck/build | pass; 2,417 modules; route-split entry 209.08 kB and largest lazy route 388.72 kB |
| Strict startup | passes against fresh migrated PostgreSQL with 36 routes |
| Phase 1F backend acceptance | **14 passed**, including the migrated PostgreSQL morning journey |
| Phase 1F browser acceptance | **14 passed**, 4 intentional cross-viewport matrix duplicates skipped; desktop/tablet/mobile |
| Workflow security | **5 passed**; manual live smoke has no secret, DB, schedule, PR, or push path |
| Live competitor coverage | **10 healthy / 2 failed**; 9,376 products across healthy stores; Shopbloxs and BloxCrew GraphQL healthy |
| Dependency audit | **4 advisories**: 1 low, 3 moderate, **0 high/critical** |

Known pre-existing warnings remain: Pydantic class-based config, FastAPI `on_event`, the
custom pytest-asyncio loop fixture, Starlette's multipart import, React Router v7 future
flags, and Vite's CJS Node API. The former monolithic frontend chunk warning is resolved by
route-level splitting. Frontend lint is not an available gate because ESLint is not installed.

## 8. Remaining risks

- GitHub schedules are best-effort and can be delayed or disabled after repository
  inactivity. Manual runner startup also has queue/setup latency.
- A manual run that enters `retry_wait` may need a later manual or scheduled invocation;
  PostgreSQL preserves it, but GitHub Actions is not a persistent poller.
- Completeness depends on adapter evidence. The final-full-page rule is conservative, but
  a storefront that silently truncates without pagination evidence can still be misread.
- Notifications remain legacy and are not yet transactional/outbox-backed. Sync success is
  intentionally independent of Discord delivery.
- The old scraper remains a multi-platform service; Phase 1B.2 added a contract/telemetry
  boundary without performing the later adapter refactor.
- Production classification is **B-** and the duplicate audit is complete. Backup/restore
  proof, duplicate remediation, migrations, provider configuration, and smoke remain
  blocked; see `docs/PHASE_1E_1_RELEASE_UNBLOCK.md`.
- Search suggestions still use an explicit 1,000-candidate cap; broad queries ask the user
  to add a word rather than claiming complete suggestion coverage.
- The market taxonomy remains hardcoded and duplicated with Export/scraper vocabulary.
- Search trust follows the accepted Cairo morning topology; a future promised cadence
  requires a policy/test update.
- Snapshot history records changes, not every observation, so Search shows recent change
  context rather than a dense price series.
- Export URL validation rejects malformed, credentialed, foreign, localhost, and literal
  private targets, but DNS rebinding and cross-host redirects remain adapter-level limits.
- Export remains a synchronous request. A slow provider can still meet the existing adapter
  timeout but exceed an eventual deployment request ceiling; production proof must exercise
  a bounded known collection.
- BuyBlox currently fails safe as `temporary_network`; do not disable certificate validation
  to make it green. TubbysTubby's canonical host currently serves no catalog. Production
  coverage is therefore incomplete even apart from the existing Case B- database blockers.
- Public Storefront-token discovery remains an explicit legal/ToS and false-positive risk.
  Phase 1F added a narrow adjacent-endpoint pattern but retained the older hex fallback;
  see `docs/SECURITY.md` §1.5.
- React Router v6 retains three moderate audit findings. A v7 upgrade is a separately tested
  migration, not a safe lockfile-only patch.

## 9. Next recommended task

After final protected-Preview smoke, the owner should complete the five-part checklist in
`docs/PREVIEW_TESTING.md`: Search, Exports, Competitors/Sync, desktop fit, and mobile fit.
The Production recommendation is **HOLD**: resolve the documented Case B- database release
blockers and the BuyBlox/TubbysTubby coverage gaps before considering rollout. Do not merge
to `main`, change Production configuration/data, or start the release-unblock runbook
without new authorization.

## 10. Decisions not to reverse

1. Alembic is the only schema authority; startup verifies and never mutates schema.
2. PostgreSQL is the durable Sync coordination/source-of-truth layer; Redis is not.
3. Product identity is canonical URL plus derived product key, never title.
4. Absence inference requires complete, newer catalog coverage.
5. Actual external observation time orders product state; run start is not freshness.
6. Claims require a committed lease and fencing token; no transaction spans acquisition.
7. `DB_SCHEMA_CHECK=warn` remains until production is verified and migrated.
8. No live network calls in deterministic tests or required push/PR CI, and no secrets in
   logs, fixtures, artifacts, or docs. Manual observational coverage is the only exception.
9. `archive/pre-v2-rearchitecture` must not be changed.
10. Preserve Git author identity and do not add AI attribution trailers.
11. Search never hides degraded observations or promotes them to the reliable market
    reference; currencies remain separate.
12. `SYNC_MORNING_ENABLED` stays false through the single-competitor and manual Sync-All
    proof; Vercel compatibility cron is never a second automatic Sync owner.

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
npm run test:e2e
```
