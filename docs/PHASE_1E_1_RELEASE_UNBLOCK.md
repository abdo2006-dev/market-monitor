# Phase 1E.1 Production Security and Release Unblock

Status as of 2026-08-13. This is a read-only evidence record and an operator plan. It
does not authorize a push, merge, deployment, secret change, schema/data write, or Sync.
The credential exposed during Phase 1E was not recovered, searched for, printed, or
tested.

## A. Credential-rotation readiness — VERIFIED

- The old temporary file `/private/tmp/mm_phase1e_production.env` is absent.
- No production database connection string is tracked at `16d3b91`; the only tracked
  connection strings are explicit local-development examples.
- `app.config.Settings.DATABASE_URL` reads the production connection from the environment.
- A redacted current-commit Gitleaks scan passed.
- The current Vercel `DATABASE_URL` successfully completed trusted-TLS, read-only catalog
  queries on 2026-08-13. This proves that Vercel currently has a valid credential. It does
  not test the forbidden old credential or independently prove that the old credential is
  invalid.

## B. Production secret/config readiness — BLOCKED

Only names, presence, and non-secret metadata were inspected.

### Vercel

| Item | State | Evidence |
|---|---|---|
| Production database connection | PRESENT | Current credential completed read-only queries. |
| `DB_SCHEMA_CHECK` | ABSENT | V2 code default is `warn`; deployed code is still pre-V2. |
| `SYNC_EXECUTION_MODE` | ABSENT | V2 code default is `v2`; deployed code is still pre-V2. |
| `SYNC_DISPATCH_PROVIDER` | ABSENT | V2 code default is `none`. |
| `SYNC_MORNING_ENABLED` | ABSENT | V2 code default is false. |
| GitHub dispatcher token/repository | ABSENT | Correct for the initial manual rollout. |
| `CRON_SECRET` | ABSENT | Blocker while the deployment is public. |
| Production deployment | PRESENT | Ready, but default GitHub `main` is still `f346f70` and has no V2 commits. |

The application has no authentication and the deployment is public. Deployment
protection or an authenticating proxy is required before exposing the V2 request routes,
unless the owner explicitly accepts anonymous durable-work creation. `CRON_SECRET` is
also required before retaining the compatibility cron routes.

### GitHub

| Item | State | Evidence |
|---|---|---|
| `production-sync` environment | ABSENT | Only `Production` exists. |
| Environment `PRODUCTION_DATABASE_URL` | ABSENT | No repository/environment secret is configured. |
| Environment branch restriction | ABSENT | No `production-sync` environment exists. |
| Required reviewer for routine schedule | ABSENT | Desired eventual state, but no schedule exists on `main`. |
| Workflows on default `main` | ABSENT | GitHub reports zero workflows. |
| Repository `SYNC_MORNING_ENABLED` variable | ABSENT | Safe/default-off. |
| `main` protection | ABSENT | Cumulative release PR cannot be policy-enforced yet. |
| Local workflow permissions | PRESENT | `contents: read`; actions are SHA-pinned and checkout credentials are not persisted. |

`production-sync` must restrict deployments to protected/default `main`. It must not have
a required reviewer when Stage 5 unattended mornings are enabled, because that would turn
the routine schedule into a manual approval queue.

## C. Backup readiness — BLOCKED

The actual provider is the Vercel-managed Neon resource `neon-chestnut-pillar`, reported
healthy on the Neon Free plan. Neon provides continuous history / point-in-time restore.
The current Free plan advertises a restore window of up to six hours or 1 GB of changes:
[Neon pricing](https://neon.com/pricing). The restore window is project-configurable in
Settings -> Restore window: [Neon project settings](https://neon.com/docs/manage/projects).

The authenticated Vercel CLI does not expose this project's configured restore window,
oldest recoverable timestamp, latest recovery point, or snapshot list. The browser
session was not authenticated to the provider dashboard. Consequently these remain
unverified:

- configured retention (do not substitute the plan maximum);
- current oldest/latest recoverable timestamps;
- existence and timestamp of a manual/scheduled snapshot;
- a recovery artifact outside the same provider failure domain.

Before any write, the owner must open Neon Backup & Restore, record the configured window
and current recoverable timestamp, and take a named pre-migration snapshot or a verified
logical dump in an owner-approved secure destination. Scheduled snapshots are a paid-plan
feature; Free-plan PITR is not a substitute for a durable off-provider backup.

## D. Restore-test evidence — BLOCKED

No restore was run. A proposed full logical dump to a protected temporary directory was
rejected by the execution environment because it would copy production data to a local
destination without narrower data-egress authorization. It was not bypassed, and no dump
was created. Provider restore tooling was unavailable without an authenticated Neon
dashboard/API session.

A safe proof must restore a named snapshot or logical dump into a separate disposable
branch/database, never over production, and verify only:

```sql
SELECT current_database();
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
SELECT count(*) FROM competitors;
SELECT count(*) FROM products;
SELECT count(*) FROM product_snapshots;
SELECT count(*) FROM events;
SELECT count(*) FROM scrape_runs;
```

Require the six Phase-1A core tables, readable rows, and counts plausibly close to the
read-only production baseline. Delete the disposable target only after recording the
restore source, recovery timestamp, start/end time, and sanitized counts.

## E. TLS verification — VERIFIED

- The active Vercel URL has `sslmode=require`; no disabled/allow/prefer mode is present.
- `app.database._normalize_database_url()` converts that production path to asyncpg
  `ssl=True`. Asyncpg documents `True` as a default `SSLContext` with CA and hostname
  verification (verify-full behavior).
- The read-only classifier and audits succeeded only after using the installed Certifi CA
  bundle. Verification was not disabled or weakened.
- The endpoint is a Neon pooled endpoint. `pg_stat_ssl` describes the pooler-to-Postgres
  backend leg, not the client-to-pooler TLS handshake, so its `ssl=false` row is not valid
  client-TLS evidence. Client context construction and successful certificate validation
  are the applicable evidence.

The active production URL follows the verified path. A future production URL must retain
`sslmode=require`, `verify-ca`, or `verify-full`; `sslmode=disable` is a release stop.

## F. Schema classification — VERIFIED

The documented classifier ran read-only, through trusted TLS, with the new current Vercel
credential. Result: **B-**.

Non-sensitive reason: the database has the exact six-table Phase 1A / revision `0003`
shape, no `alembic_version` table, all expected pre-`0004` columns, no unexpected tables,
and no unexpected columns. It truthfully lacks the Phase 1B.1 identity columns and Phase
1B.2 durable Sync tables/columns. Do not stamp head; after backup, stamp exactly
`0003_reconcile_index_names`.

## G. Duplicate audit — VERIFIED

The existing audit ran inside `SET TRANSACTION READ ONLY`. No URLs or row details were
reported.

| Aggregate | Count |
|---|---:|
| Products inspected | 12,837 |
| Exact URL duplicate groups | 0 |
| Raw external-identity duplicate groups | 1 |
| Canonical URL duplicate groups | 11 |
| Derived identity-key duplicate groups | 2 |
| Transitive canonical-identity groups | 12 |
| Affected product rows | 24 |
| Snapshot rows associated with those groups | 55 |
| Event rows associated with those groups | 62 |
| Total associated history rows | 117 |
| Invalid identity rows | 0 |

Migration `0004` is blocked until the 12 logical groups are explicitly consolidated and
the audit returns zero logical groups. The consolidation must retain all 117 history rows
and repoint them; it must not delete or fabricate history.

At inspection time there were zero legacy `running` rows and zero competitors with
overlapping running rows. This is time-sensitive and must be repeated immediately before
`0005`.

## H. Historical Storefront token classification — VERIFIED

The literal introduced in commit `9660f6b` was used as the value of
`X-Shopify-Storefront-Access-Token`. Shopify defines that header for **public** Storefront
API tokens used by client-side storefronts. Private Storefront tokens use the distinct
`Shopify-Storefront-Private-Token` header and must remain server-side:
[Shopify authentication](https://shopify.dev/docs/api/usage/authentication).

Classification: **Shopify public Storefront token**. The literal was removed by
`8e5f78c`; current source contains no hard-coded Storefront token. It is intended for
public storefront access, so repository-history rewriting solely for secrecy is not
recommended. The separate ToS/automation question in `SECURITY.md` remains.

## I. Exact migration plan — READY BUT NOT EXECUTED

This plan is valid only while the classifier remains B-. Case C stops the entire plan.
Every write requires separate owner authorization.

### 1. Maintenance and backup checkpoint

1. Protect the deployment from anonymous requests and pause the legacy Vercel cron for a
   maintenance window.
2. Confirm no external Celery/legacy worker is running.
3. In Neon Backup & Restore, record the configured restore window and a current recovery
   timestamp. Create a named pre-migration snapshot when available.
4. Create an off-provider logical backup in an owner-approved secure destination using
   libpq variables so certificate and hostname verification cannot be weakened by a URL
   query parameter:

```bash
export PGHOST='<neon-host>'
export PGPORT='5432'
export PGDATABASE='<database>'
export PGUSER='<role>'
read -rs PGPASSWORD && export PGPASSWORD
export PGSSLMODE='verify-full'
export PGSSLROOTCERT="$(python -c 'import certifi; print(certifi.where())')"
export BACKUP_DIR='/owner-approved/encrypted-or-access-controlled-location'
export BACKUP_FILE="$BACKUP_DIR/market-monitor-pre-0004-$(date -u +%Y%m%dT%H%M%SZ).dump"
umask 077
pg_dump --format=custom --no-owner --no-acl --file="$BACKUP_FILE"
pg_restore --list "$BACKUP_FILE" >/dev/null
shasum -a 256 "$BACKUP_FILE"
```

5. Restore that artifact into a disposable database/Neon branch and run the queries in
   section D. Do not continue until the restore is readable and documented.

### 2. Reclassify and stamp truthfully

```bash
cd backend
export DATABASE_URL='<current-production-async-url>'
export SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi; print(certifi.where())')"
.venv/bin/python scripts/check_schema_state.py
```

Require **B-** / exit 21, then:

```bash
.venv/bin/python -m alembic stamp 0003_reconcile_index_names
.venv/bin/python scripts/check_schema_state.py
```

Require **A-** at `0003`. Never use `stamp head` for this database.

### 3. Remediate duplicates before `0004`

Keep every plan outside the repository in the same protected backup location:

```bash
.venv/bin/python scripts/audit_product_duplicates.py --json \
  > "$BACKUP_DIR/product-audit-pre-merge.json"
.venv/bin/python scripts/consolidate_product_duplicates.py
.venv/bin/python scripts/consolidate_product_duplicates.py \
  --apply \
  --backup-confirmed \
  --confirm MERGE_DUPLICATE_PRODUCTS \
  --plan-json "$BACKUP_DIR/product-merge-plan.json"
.venv/bin/python scripts/audit_product_duplicates.py --json \
  > "$BACKUP_DIR/product-audit-post-merge.json"
```

Require zero logical duplicate groups, zero affected products, zero invalid identities,
and unchanged combined snapshot/event row counts for the remediated groups.

### 4. Upgrade and verify `0004`

```bash
.venv/bin/python -m alembic upgrade 0004_product_identity_integrity
.venv/bin/python -m alembic current
.venv/bin/python scripts/audit_product_duplicates.py --json
```

Require revision `0004_product_identity_integrity`, non-null `canonical_url`, and both
`uq_products_competitor_canonical_url` and
`uq_products_competitor_identity_key`.

### 5. Recheck active legacy work, then upgrade `0005`

Using the already trusted `PG*` variables:

```bash
psql -v ON_ERROR_STOP=1 -c "
SELECT competitor_id, array_agg(id ORDER BY id) AS run_ids
FROM scrape_runs
WHERE status = 'running'
GROUP BY competitor_id
HAVING count(*) > 1;"
```

Require zero rows. Resolve only confirmed abandoned legacy work; never bulk-update a live
worker's row. Then:

```bash
.venv/bin/python -m alembic upgrade 0005_durable_sync_lifecycle
.venv/bin/python -m alembic current
.venv/bin/python scripts/check_schema_state.py
```

Require `0005_durable_sync_lifecycle (head)` and Case A. Verify `sync_requests`,
`sync_request_runs`, lifecycle checks, lineage foreign keys,
`uq_scrape_runs_competitor_non_terminal`, and `ix_scrape_runs_claimable`.

### 6. Strict mode

Deploy initially with `DB_SCHEMA_CHECK=warn`. Change it to `strict` only after the
one-competitor smoke succeeds, then redeploy and require a healthy startup. Strict mode is
not a substitute for the post-migration classifier.

## J. V2 rollout flags — BLOCKED

| Stage | Required configuration and proof |
|---|---|
| 0 — no automatic V2 | Current release not integrated; GitHub `SYNC_MORNING_ENABLED` absent/false; no V2 worker. |
| 1 — deployed/manual only | Schema Case A; protected deployment; `SYNC_EXECUTION_MODE=v2`; `SYNC_DISPATCH_PROVIDER=none`; `SYNC_MORNING_ENABLED=false`; `DB_SCHEMA_CHECK=warn`; GitHub environment present; no dispatcher token. |
| 2 — one competitor | Same flags; create one TubbysTubby request and run one manual request-id workflow. No schedule. |
| 3 — manual V2 | Keep dispatcher `none`; operator may use per-competitor manual Sync after Stage 2 proof. |
| 4 — Sync All | Same flags; operator explicitly enables/uses Sync All only after single-request proof. |
| 5 — morning | Set only GitHub repository variable `SYNC_MORNING_ENABLED=true`; keep Vercel `SYNC_MORNING_ENABLED=false`; no required environment reviewer. |

Stages 3 and 4 are not independently represented by backend feature flags: the deployed
V2 API exposes both single and all request routes. Until dedicated gates exist, separation
depends on deployment authentication and operator discipline. This is unacceptable on an
anonymous public deployment and remains a release blocker.

`SYNC_EXECUTION_MODE` is the compatibility-route coexistence switch. In `v2`, legacy
manual/cron execution paths and the Celery scan scheduler do not process intended V2
requests. Before any rollback to `legacy`, stop the V2 worker and require every queued,
running, or retry-wait V2 run to be terminal; otherwise two systems could touch one
competitor even though they did not claim the same durable request row.

## K. One-competitor smoke plan — READY BUT NOT EXECUTED

Use TubbysTubby, competitor ID `10`. The read-only baseline on 2026-08-13 was: active,
145 total/active products, zero logical duplicate groups, zero affected duplicate rows,
zero removal events, 17 historical runs, and latest successful freshness at
2026-08-10T20:16:23Z. Re-baseline immediately before the smoke.

### Before

```sql
SELECT c.id, c.active, c.last_scan_at, c.last_scan_status,
       count(p.id) AS products_total,
       count(p.id) FILTER (WHERE p.active) AS products_active
FROM competitors c
LEFT JOIN products p ON p.competitor_id = c.id
WHERE c.id = 10
GROUP BY c.id;

SELECT count(*) AS canonical_duplicate_groups
FROM (
  SELECT canonical_url
  FROM products
  WHERE competitor_id = 10
  GROUP BY canonical_url
  HAVING count(*) > 1
) duplicate_groups;

SELECT count(*) AS removal_events
FROM events
WHERE competitor_id = 10 AND event_type = 'product_removed';
```

Save sanitized counts and select one currently active product ID/title for Search proof.

### Run exactly once

```bash
export MARKET_MONITOR_API='https://market-monitor-six.vercel.app'
curl -sS -X POST \
  -H 'Idempotency-Key: production-smoke-tubbys-1' \
  "$MARKET_MONITOR_API/api/sync/competitors/10"
gh workflow run sync-v2.yml --ref main -f request_id='<request-uuid>'
```

Do not enable API dispatch; exactly one manually started GitHub runner owns the request.

### Verify lifecycle and reconciliation

```bash
curl -sS "$MARKET_MONITOR_API/api/sync/requests/<request-uuid>"
curl -sS "$MARKET_MONITOR_API/api/sync/runs/<run-id>"
```

Require queued -> running -> one terminal state, sensible `complete` or conservative
`partial` completeness, non-zero observed products, and no retry/claim ambiguity. A fast
run may pass through `running` between polls.

```sql
SELECT id, status, completeness, products_found, pages_fetched, page_cap_reached,
       queued_at, claimed_at, acquisition_started_at, acquisition_completed_at,
       reconciled_at, terminal_at
FROM scrape_runs
WHERE id = <run-id>;

SELECT count(*) AS products_from_run,
       min(last_observed_at) AS first_observation,
       max(last_observed_at) AS last_observation
FROM products
WHERE competitor_id = 10 AND last_observed_run_id = <run-id>;

SELECT count(*) AS snapshots_with_lineage
FROM product_snapshots
WHERE scrape_run_id = <run-id>;

SELECT event_type, count(*)
FROM events
WHERE scrape_run_id = <run-id>
GROUP BY event_type
ORDER BY event_type;
```

Require observation timestamps inside the acquisition window, snapshot/event lineage to
the run where changes occurred, no duplicate creation, and no mass `product_removed`
event. Complete coverage may produce legitimate changes; partial/suspicious coverage must
not infer absence.

### Search and Export proof

- Open Search for the saved product and require the new observation/run, current coverage,
  freshness age, completeness, and reliable/degraded pricing rules to agree with the run.
- Prepare one bounded **Live** Export and require live source, new observation time,
  completeness/page/cap evidence, and no cached substitution.
- Prepare **Latest stored data** separately and require cached source, real observation
  range, latest complete/terminal run lineage, coverage state, and legacy/mixed-lineage
  counts. Do not describe cached output as live.

### After

Repeat all Before queries. Product totals should remain non-empty and plausible, canonical
duplicates must remain zero, and removal-event delta must not indicate a mass removal.
Compare run-scoped snapshot/event counts, then inspect only the GitHub worker's structured
`run_claimed`, `run_finished`, lease recovery, and failure-category logs. Logs must not
contain database URLs, tokens, webhook URLs, or competitor configuration secrets.

## L. Remaining blockers — BLOCKED

1. Prove the configured Neon restore window/current recovery point and complete a separate
   disposable restore test.
2. Create a current named backup/snapshot in an approved destination before any write.
3. Remediate 12 logical duplicate groups affecting 24 products while preserving 117
   history rows.
4. Create GitHub `production-sync`, its environment-only database secret, and main-only
   deployment policy; integrate workflows onto reviewed/protected `main`.
5. Protect the public deployment (or explicitly accept the anonymous write risk) and set
   `CRON_SECRET`.
6. Decide whether to add independent manual-single/Sync-All feature gates; current flags
   cannot enforce Stage 3 versus Stage 4.
7. Obtain separate authorization for backup creation, stamping, duplicate consolidation,
   migrations, config changes, deployment, and every smoke/Sync stage.
