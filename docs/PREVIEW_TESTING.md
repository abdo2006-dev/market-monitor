# V2 user-testing preview

_Created: 2026-08-15. This is a Preview deployment, not Production._

## Open the preview

Vercel Preview URL:

`https://market-monitor-git-preview-market-d681d2-abdo2006-devs-projects.vercel.app`

Latest Phase 1F deployment (READY, target `null`, never Production):

`https://market-monitor-297ruaafk-abdo2006-devs-projects.vercel.app`

Vercel Deployment Protection is enabled. Sign in with the owner Vercel account when the
URL redirects to Vercel SSO.

## Isolation and execution model

- Git branch: `preview/market-monitor-v2`; `main` is unchanged at `f346f70`.
- Database: Vercel Marketplace Neon resource `market-monitor-v2-preview-db`, a separate
  Free-plan Neon project connected only to Vercel Preview. It was created empty; no
  production rows were cloned or copied.
- Schema: migrated from empty through Alembic head `0005_durable_sync_lifecycle`.
- Startup: `DB_SCHEMA_CHECK=strict`.
- Sync: `SYNC_EXECUTION_MODE=v2`, dispatcher `none`, morning disabled, inline legacy
  execution disabled. There is no preview worker. Manual requests are durable preview DB
  writes and remain queued; they do not scrape or reconcile.
- Notifications: disabled. The production Discord credential is not in Preview.
- Cron: automatic morning Sync is disabled and Preview has its own secret.
- Fixture acquisition: requires all three gates: `PREVIEW_DEMO_MODE=true`, Vercel's
  reserved `VERCEL_ENV=preview`, and `selector_config.preview_demo=true` on the row. The
  production deployment cannot enable the fixture by setting the feature flag alone.
- The seed script additionally verifies Alembic head and refuses DML unless the database
  is either empty with the one-time `PREVIEW_ALLOW_INITIALIZE_EMPTY=true` gate, or contains
  exactly seven marker-owned competitors and no unrelated rows. Environment labels alone
  are not treated as proof of database identity.

On 2026-08-16, a local `vercel env run` refresh attempt found no branch-scoped CLI
variables and fell back to the local environment. The first connection failed TLS before
querying; the trusted-TLS retry encountered the non-`0005` schema during its transaction,
which rolled back without commit. No persistent seed change occurred. The schema/content
preflight above was added immediately afterward. Do not retry through CLI environment
fallback; use a positively identified Preview database path or leave the evidence stale.

Production environment variables were pulled before and after preview configuration.
Every user-managed Production value, including `DATABASE_URL`, was unchanged; only
Vercel's automatically rotating `VERCEL_OIDC_TOKEN` differed.

## Seeded data

The idempotent seed script creates seven competitors and 21 products. Every store uses a
reserved `.invalid` hostname and contains no credential, webhook, or real storefront data.

| Competitor | Search/Sync evidence | Live Export fixture |
|---|---|---|
| Preview Alpha — complete + queued | current complete, reliable in-stock price, queued Sync, recent decrease | complete |
| Preview Beta — partial + retrying | partial/degraded price, retry-wait Sync | partial/page cap |
| Preview Gamma — failed | older observation with latest failed coverage | structured 502 failure |
| Preview Delta — suspicious empty | prior price degraded by suspicious-empty latest run | suspicious empty |
| Preview Epsilon — legacy observation | no V2 lineage, unknown age/reliability | complete fixture; cached rows remain legacy |
| Preview Omega — complete + running | current out-of-stock price excluded from reliable range, running Sync | complete |
| Preview Zeta — current complete | current reliable in-stock price | complete |

All competitors carry `Batwing`, `Elderwood Scythe`, and `Harvester`. `Batwing` is the
primary Search example. Alpha's reliable price is `$84.99`; Zeta's is `$89.00`. Lower
observed prices are deliberately degraded (partial, failed, legacy, suspicious-empty, or
out of stock), so the UI demonstrates why observed-low and reliable-low differ.

## Owner test script

1. Open **Market Search**, enter `Batwing`, use Arrow Down/Up if desired, and press Enter.
   If the fixture was seeded in the current Cairo cycle, confirm Alpha's reliable low is
   `$84.99`. Otherwise confirm Search truthfully reports no reliable current price and
   marks the older complete rows stale; it must never silently promote them. Expand a card
   to inspect run lineage and the recent price drop.
2. Open **Exports**. Choose **Preview Alpha — complete + queued**; its collection URL is
   filled automatically. Prepare Live CSV, JSONL, or JSON and confirm it is complete, then
   explicitly choose Latest stored data and compare the source label.
3. Choose **Preview Beta — partial + retrying** and prepare Live. Confirm two products,
   page-cap warnings, and the explicit partial download button.
4. Choose **Preview Gamma — failed** and prepare Live. Confirm the safe failure panel and
   that stored data is offered only as a separate explicit action. Choose **Preview Delta
   — suspicious empty** to inspect the zero-product warning.
5. Open **Competitors**. Confirm the readiness summary and inspect queued, running, retrying,
   success, partial, suspicious-empty, failed, and legacy states. Press Sync on Zeta if
   desired: the response is accepted/queued with dispatcher `not requested`; it never claims
   completion. Confirm destructive actions remain secondary in each row's action menu.
6. Repeat Search, Exports, and Competitors at a normal desktop width. Confirm the primary
   action and evidence hierarchy are clear and the page never scrolls horizontally.
7. Repeat all three workflows on a phone-sized viewport. Confirm the bottom workflow
   navigation, More drawer, menus, controls, and evidence remain usable without clipping.

Record the verdict in this order: **Search → Exports → Competitors/Sync → desktop → mobile**.
This is an owner-testing gate, not authorization to merge, migrate, or enable Production.

## Verification evidence

- Deployed `/api/health`: `{"status":"ok"}`.
- Deployed Search: one `Batwing` group, seven competitors, two reliable USD prices, four
  degraded/unknown rows, and three active lifecycle examples at initial seed.
- Deployed Export: partial Live returned two products and `page_cap_reached=true`; failed
  Live returned structured 502; explicit cached returned three rows with source `cached`.
- Deployed Sync smoke: one manual request returned `status=queued`,
  `dispatch_status=not_requested`, `completeness=unknown`, and zero observed products.
- Browser checks: autocomplete keyboard selection, comparison cards, price-change context,
  Export complete/partial/suspicious-empty/failure/stored panels, Sync lifecycle operations
  center, More-drawer keyboard dismissal, 1440/1280/1024/961/959/768/390 px widths, no
  horizontal document overflow, and no console errors.
- Frontend: 19 component tests, 14 Playwright journeys across desktop/tablet/mobile,
  typecheck, and production build pass. The shell and routes are lazy loaded; the prior
  711.25 kB monolithic JavaScript bundle is replaced by a 210.69 kB shared entry plus route
  chunks (largest lazy route 398.73 kB).
- Full backend: 237 tests pass against a separate disposable local PostgreSQL database.
- Critical backend: 143 tests pass. Search: 39. Export/shared policy: 28. Product integrity/
  concurrency: 37. Preview/security release gate: 9.
- Fresh migration round-trip reached `0005_durable_sync_lifecycle`; `alembic check` reports
  no new upgrade operations. Strict application startup passes with 36 routes.

Phase 1F's deterministic/browser checks make no live storefront request. The separate
manual read-only coverage matrix is documented in `docs/COMPETITOR_COVERAGE.md`. The
disposable local database is separate from both Preview Neon and Production. The failed
CLI seed transaction described above rolled back without commit; Production configuration
and persistent data were not changed.
