# V2 user-testing preview

_Created: 2026-08-15. This is a Preview deployment, not Production._

## Open the preview

Vercel Preview URL:

`https://market-monitor-git-preview-market-d681d2-abdo2006-devs-projects.vercel.app`

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
   Confirm the reliable low is Alpha at `$84.99`, while lower observed prices stay visible
   with degraded evidence. Expand a card to inspect run lineage and the recent price drop.
2. Open **Exports**. Choose **Preview Alpha — complete + queued**; its collection URL is
   filled automatically. Prepare Live CSV, JSONL, or JSON and confirm it is complete, then
   explicitly choose Latest stored data and compare the source label.
3. Choose **Preview Beta — partial + retrying** and prepare Live. Confirm two products,
   page-cap warnings, and the explicit partial download button.
4. Choose **Preview Gamma — failed** and prepare Live. Confirm the safe failure panel and
   that stored data is offered only as a separate explicit action. Choose **Preview Delta
   — suspicious empty** to inspect the zero-product warning.
5. Open **Competitors**. Inspect queued, running, retrying, success, partial,
   suspicious-empty, failed, and legacy states. Press Scan on Zeta if desired: the response
   is accepted/queued with dispatcher `not requested`; it never claims completion.

## Verification evidence

- Deployed `/api/health`: `{"status":"ok"}`.
- Deployed Search: one `Batwing` group, seven competitors, two reliable USD prices, four
  degraded/unknown rows, and three active lifecycle examples at initial seed.
- Deployed Export: partial Live returned two products and `page_cap_reached=true`; failed
  Live returned structured 502; explicit cached returned three rows with source `cached`.
- Deployed Sync smoke: one manual request returned `status=queued`,
  `dispatch_status=not_requested`, `completeness=unknown`, and zero observed products.
- Browser checks: autocomplete keyboard selection, comparison cards, price-change context,
  Export partial/failure panels, Sync lifecycle table, desktop width, 390px Search/Export/
  Competitors width, and no console errors.
- Frontend: 17 tests, typecheck, and production build pass.
- Preview/acquisition/release unit selection: 10 tests pass.

The full database suite was attempted against a separate disposable database inside the
preview Neon project, but the remote run exceeded this execution environment's 30-second
command window before producing a terminal pytest summary. It is not reported as a pass.
The deployed migrated schema and all three daily workflows were instead exercised directly
against the isolated preview database as listed above.
