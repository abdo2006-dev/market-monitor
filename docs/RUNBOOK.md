# Runbook

Operational procedures. Everything here reflects the system as it exists at `f346f70` —
including its current rough edges.

---

## 1. Local development

### Full stack (recommended)

```bash
docker compose up --build
```

Frontend `http://localhost:3000` · API `http://localhost:8000` · docs
`http://localhost:8000/docs`. Migrations run automatically in the `backend` service.

### Backend only

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Requires PostgreSQL and Redis reachable at the defaults. **Note**: `.env` at the
repository root is *not* loaded when running from `backend/` (see
`docs/DEPLOYMENT.md` §3). Export variables explicitly:

```bash
cd backend && DATABASE_URL="postgresql+asyncpg://market:market@localhost:5432/market_monitor" .venv/bin/python -m uvicorn app.main:app --reload
```

### Frontend only

```bash
cd frontend && npm run dev
```

Vite proxies `/api` to `http://localhost:8000` (`vite.config.ts`).

### Worker and scheduler

```bash
cd backend && .venv/bin/python -m celery -A app.workers.celery_app worker --loglevel=info --concurrency=2
```

```bash
cd backend && .venv/bin/python -m celery -A app.workers.celery_app beat --loglevel=info
```

---

## 2. Database operations

### Check migration state

```bash
cd backend && .venv/bin/python -m alembic current
```

**If this prints nothing** (no revision), the database was created by the application's
`init_db()` rather than by Alembic and has no `alembic_version` table. See §2.2 — do not
simply run `upgrade head`, it will fail.

### Apply migrations

```bash
cd backend && .venv/bin/python -m alembic upgrade head
```

### 2.2 Classifying and repairing a database (Cases A / A- / B / B- / C / D)

Since Phase 1A the application no longer creates schema at startup. Alembic is the sole
authority (`docs/adr/0002`). Before touching any database, **classify it**. Never stamp
blindly.

#### Step 1 — Inspect (read-only, always safe)

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python scripts/check_schema_state.py
```

This script only reads catalog metadata. It emits a case and an exit code:

| Case | Exit | Meaning | Action |
|---|---|---|---|
| **A** | 0 | Managed by Alembic, at head | Nothing to do |
| **A-** | 10 | Managed by Alembic, behind head | `alembic upgrade head` |
| **B** | 20 | Schema structurally matches the models, but no `alembic_version` stamp | Backup, then stamp — Step 3 |
| **B-** | 21 | Unstamped schema matches Phase 1A revision `0003` | Backup, stamp **exactly `0003`**, then continue below |
| **C** | 30 | **Real drift**: tables or columns missing/unexpected | **Do NOT stamp** — Step 4 |
| **D** | 40 | Empty database | `alembic upgrade head` |

#### Step 2 — Back up before anything that writes

Non-negotiable for Cases B, B-, and C.

```bash
pg_dump "$DATABASE_URL" > backup-$(date +%F-%H%M).sql
```

Store it outside the repository. Never commit a dump.

#### Step 3 — Case B: structurally correct but unstamped

This is the expected state of a database created by a pre-Phase-1A build, where startup
ran `create_all`. The schema is right; only the bookkeeping row is missing.

The inspector has already confirmed that every table and column the models declare is
present. Re-read its output and confirm `missing tables` and `missing columns` are both
empty before continuing.

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python -m alembic stamp head
```

`stamp` writes one row to `alembic_version`. **It runs no DDL** and cannot alter your data.

Verify:

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python scripts/check_schema_state.py
```

Expect Case A.

#### Step 3A — Case B-: unstamped Phase 1A schema

After Phase 1B.1, an unstamped pre-integrity database legitimately lacks the two product
identity columns. The current inspector reports **B-**, not drift. Back up, then stamp the
revision the schema actually represents:

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python -m alembic stamp 0003_reconcile_index_names
```

Do **not** stamp `head`: that would falsely mark migration `0004` applied and skip its
backfill and constraints. Re-run the inspector; expect A-, then follow §2.3 below.

Note on index names: a `create_all`-built database already uses the model naming
(`ix_product_snapshots_*`). Migration `0003_reconcile_index_names` converges the two
historical schemes and is written to be a no-op on a database that is already correct, so
stamping past it is safe.

#### Step 4 — Case C: real drift. Stop.

The database has tables or columns the models do not expect, or is missing ones they
require. **Stamping would tell Alembic a lie** and every future migration would be applied
to a schema it does not describe.

1. Do not stamp. Do not run `upgrade head`.
2. Re-run the inspector with `--json` and keep the output.

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python scripts/check_schema_state.py --json > drift.json
```

3. Decide, per difference, whether the database or the models are correct.
4. Reconcile by hand with explicit SQL, or write a migration that brings the database to
   the expected shape, then stamp the revision *before* that migration and upgrade.
5. Only once the inspector reports Case B, B-, or A may you continue.

If the drift is small and the data is expendable, restoring from backup into a freshly
migrated database is often faster and safer than hand-reconciliation.

#### Applying migrations to production

Vercel runs no migration step (`docs/DEPLOYMENT.md` §2). Two supported routes:

**Manual, from a trusted machine:**

```bash
cd backend && DATABASE_URL="<production>" .venv/bin/python -m alembic upgrade head
```

**Via the manual GitHub Actions workflow** (`.github/workflows/db-migrate.yml`):
Actions → "Database migration (manual)" → Run workflow. It defaults to `inspect`
(read-only). Write actions require selecting `upgrade` or `stamp-head` **and** typing
`MIGRATE` in the confirmation field. `stamp-head` refuses to run unless the inspector
reports Case B. It never runs on push, on a schedule, or on deploy.

Requires the `PRODUCTION_DATABASE_URL` repository secret.

### 2.3 Product duplicate audit and Phase 1B.1 migration

Migration `0004_product_identity_integrity` adds the product identity columns and unique
indexes. It **does not merge or delete duplicates**. If its backfill finds a conflict, the
transaction fails with affected product IDs and leaves the schema at `0003`.

First complete §2.2. The database must be Case A at `0003` or A- from `0003` to the current
head. Then run the product audit; this command is read-only and does not select competitor
configuration, webhook URLs, or other credentials:

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python scripts/audit_product_duplicates.py --json
```

The report includes exact URL, raw external-ID, canonical URL, derived product-identity,
and transitive logical duplicate groups. Each group includes affected IDs, snapshot/event
counts, first/last/check timestamps, prices, stock, active state, and misses.

If `logical_duplicate_groups` and `invalid_identity_rows` are both empty, apply normally:

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python -m alembic upgrade head
```

If duplicates exist, stop Sync and use a maintenance window. Take a fresh database backup
outside the repository, inspect the dry-run plan, then apply with all three safeguards:

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python scripts/consolidate_product_duplicates.py
```

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python scripts/consolidate_product_duplicates.py \
  --apply \
  --backup-confirmed \
  --confirm MERGE_DUPLICATE_PRODUCTS \
  --plan-json "/secure/backup/location/product-merge-plan-$(date +%F-%H%M).json"
```

The apply transaction locks product/history writes, selects a stable canonical row ID,
copies current commercial state from the most recently checked row, retains the earliest
`first_seen_at` and latest observation timestamps, repoints every `ProductSnapshot` and
`Event`, and only then deletes redundant current-state rows. It never deduplicates or
deletes snapshots/events. The mandatory JSON plan and database backup are the recovery
record for the removed duplicate current-state rows.

Re-run the read-only audit and require zero groups, then apply `alembic upgrade head` and
run the schema classifier again. Only after it reports Case A may `DB_SCHEMA_CHECK=strict`
be enabled. Do not use the GitHub `stamp-head` action for a B- database, and do not run
`upgrade` before the explicit duplicate remediation if the audit reports conflicts.

### 2.4 Phase 1B.2 durable lifecycle migration and rollout

Migration `0005_durable_sync_lifecycle` must follow `0004`; it does not replace any
classification, backup, stamping, or duplicate-remediation step above.

Before upgrading, stop all scan producers/workers and inspect active legacy rows:

```sql
SELECT competitor_id, array_agg(id ORDER BY id) AS run_ids
FROM scrape_runs
WHERE status = 'running'
GROUP BY competitor_id
HAVING count(*) > 1;
```

`0005` refuses to migrate if this returns rows. Confirm no worker owns them and resolve
each row explicitly; do not bulk-update live work. Then:

1. take/verify a fresh backup and restoration command;
2. upgrade through `0004`, re-run the duplicate audit, then upgrade to `0005`;
3. require `alembic current` to show `0005_durable_sync_lifecycle (head)`;
4. inspect `sync_requests`, `sync_request_runs`, the claimable/non-terminal indexes,
   lifecycle check constraints, and nullable snapshot/event lineage FKs;
5. deploy API/UI with `SYNC_EXECUTION_MODE=v2`, `SYNC_DISPATCH_PROVIDER=none`, and
   `DB_SCHEMA_CHECK=warn`;
6. configure the GitHub `production-sync` environment and run one manual request UUID;
7. verify request→claim→terminal status, counts, completeness, lineage, and UI polling;
8. enable optional dispatch and `DB_SCHEMA_CHECK=strict` only after proof.

Do not put the GitHub token in browser/frontend variables. A dispatch failure should remain
visible as a queued request; `python -m app.workers.sync_worker --drain` is the local/manual
recovery path.

### Create a new migration

```bash
cd backend && .venv/bin/python -m alembic revision -m "describe the change"
```

Prefer writing the migration by hand. `--autogenerate` currently produces spurious index
diffs because of the drift described above; if you use it, review every generated line and
delete the noise.

### Verify a migration round-trips

```bash
docker run -d --rm --name mm_mig_check -e POSTGRES_USER=market -e POSTGRES_PASSWORD=market -e POSTGRES_DB=market_monitor -p 55432:5432 postgres:16-alpine
```

```bash
cd backend && DATABASE_URL="postgresql+asyncpg://market:market@localhost:55432/market_monitor" .venv/bin/python -m alembic upgrade head && DATABASE_URL="postgresql+asyncpg://market:market@localhost:55432/market_monitor" .venv/bin/python -m alembic downgrade base
```

```bash
docker stop mm_mig_check
```

---

## 3. Diagnosing a failing competitor scan

### Step 1 — read the recorded failure

```sql
SELECT id, started_at, finished_at, status, products_found, left(error_message, 300)
FROM scrape_runs WHERE competitor_id = :id ORDER BY started_at DESC LIMIT 10;
```

`error_message` is truncated to 1,000 characters and carries no traceback. It is often the
only durable evidence.

### Step 2 — identify the symptom

| Symptom | Likely cause |
|---|---|
| `error_message` mentions "Scrape returned 0 products" | The scrape succeeded technically but found nothing. Go to step 3. |
| Status stuck at `running` | The process died mid-scan. Nothing reconciles this — see §4. |
| No `scrape_runs` rows at all despite pressing Scan | The scan was queued to Celery with no worker running. See §5. |
| Rows exist, products unchanged | Detection ran and found no differences. Normal. |

### Step 3 — an empty scrape

`_should_reject_empty_scrape` (`tasks.py:158`) deliberately converts an empty result into a
failure so a transient outage cannot deactivate an entire catalogue. Check, in order:

1. Is the competitor's `scrape_type` right? `_normalize_competitor_payload`
   (`api/competitors.py:185`) may have silently rewritten it on save.
2. For `shopify_json`: does `{base_url}/products.json` return JSON in a browser? Many
   storefronts disable it, in which case the run depends on the fallback chain
   (`docs/SCRAPING_ARCHITECTURE.md` §1.2).
3. For `salla_json`: was a category id resolved? With none, the scraper logs
   "No Salla category id found" and returns empty (`scraper.py:315`).
4. For `generic_selector`: is `selector_config.product_card` set? Without it the scraper
   logs a warning and breaks immediately (`scraper.py:97`).
5. If the catalogue is genuinely empty and that is expected, set
   `selector_config.allow_empty_catalog = true`.

### Step 4 — reproduce in isolation

```bash
cd backend && .venv/bin/python -c "
import asyncio, json
from app.services.scraper import scrape_competitor
c = {'id': 0, 'base_url': 'https://example.com', 'listing_urls': [],
     'selector_config': {'discover_collections': True, 'include_all_products': True},
     'scrape_type': 'shopify_json'}
r = asyncio.run(scrape_competitor(c, max_pages=1))
print(len(r)); print(json.dumps(r[:2], indent=2, default=str))
"
```

Note this scrapes a live site. Use a low `max_pages` and do not loop it.

---

## 4. Recovering a V2 running lease

Do not manually fail a V2 row merely because it looks old. Acquisition heartbeats renew
the lease, and every worker startup/drain calls the recovery use case.

Inspect:

```sql
SELECT id, competitor_id, status, attempt_count, max_attempts,
       claimed_by, heartbeat_at, lease_expires_at, now() - heartbeat_at AS heartbeat_age
FROM scrape_runs
WHERE status = 'running'
ORDER BY lease_expires_at;
```

Trigger safe recovery after confirming the expected worker is not still acquiring:

```bash
cd backend && DATABASE_URL="<target>" .venv/bin/python -m app.workers.sync_worker --once
```

Expired rows with attempts left become `retry_wait`; exhausted rows become `abandoned` and
receive a linked safe failure event. The old token is cleared/fenced. Use direct SQL only
during a documented incident with a backup and exact row review.

---

## 5. "I pressed Sync and nothing happened"

Decision path:

1. Confirm `SYNC_EXECUTION_MODE=v2`. The POST should return 202 with a request UUID and a
   `Location` header. Poll `/api/sync/requests/<uuid>`; acceptance is not completion.

2. Inspect the request and its runs:

```sql
SELECT sr.id, sr.competitor_id, sr.status, sr.attempt_count, sr.next_attempt_at,
       sr.lease_expires_at, sr.completeness, sr.failure_category, sr.error_message
FROM sync_request_runs srr
JOIN scrape_runs sr ON sr.id = srr.scrape_run_id
WHERE srr.request_id = :request_id;
```

3. If `dispatch_status=failed`, fix server-side GitHub configuration or run a trusted
   manual drain. Do not recreate/delete the request.

4. If `retry_wait`, compare `next_attempt_at` to database time. GitHub is ephemeral; a
   later manual/scheduled invocation must claim it. Railway would poll continuously.

5. If queued with `dispatch_status=not_requested`, the dispatcher is intentionally off.
   Start a local/hosted worker:

```bash
cd backend && .venv/bin/python -m app.workers.sync_worker --request-id <uuid>
```

6. If `SYNC_EXECUTION_MODE=legacy`, use the compatibility diagnosis below.

### Legacy-only diagnosis

Is a Celery worker running?

```bash
cd backend && .venv/bin/python -m celery -A app.workers.celery_app inspect active
```

   - No reply → no legacy consumer. Switch back to the proven V2 flow or restore the
     intended legacy worker; do not run both for one request.

Redis reachability matters only to legacy Celery/notification work. The V2 broker is
PostgreSQL.

---

## 6. Notification problems

### Nothing is being sent

1. `DISCORD_NOTIFICATIONS_ENABLED` must be true. Note it is **not** checked on the
   scrape-failure path (`tasks.py:151`), so failure alerts send even when disabled.
2. A webhook must resolve: `competitor.discord_webhook_url` or
   `DISCORD_DEFAULT_WEBHOOK_URL`. With neither, `dispatch_event_notifications` skips the
   event silently and leaves `notification_sent = false`.
3. On a competitor's **first** scan, or when more than 25 `new_product` events are
   pending, all `new_product` events are marked sent **without being delivered**
   (`tasks.py:110`). This is intentional flood control. Not a bug.

### Duplicate notifications

Expected consequence of A-5/A-6. Two overlapping scans of one competitor share an unscoped
`notification_sent = false` query, so both send the same events. Check for overlapping
runs:

```sql
SELECT competitor_id, count(*), min(started_at), max(started_at)
FROM scrape_runs WHERE started_at > now() - interval '1 day'
GROUP BY competitor_id HAVING count(*) > 1;
```

There is no fix available without the outbox work in Phase 1. Interim mitigation: avoid
pressing "Scan All" while the scheduler is active.

### Backlog of unsent events

```sql
SELECT event_type, count(*) FROM events WHERE notification_sent = false GROUP BY 1;
```

A large permanent `scrape_failed` count is expected — those events are never marked sent
because `dispatch_event_notifications` has no branch for them
(`docs/DATA_FLOW.md` Flow 7). Harmless, but it makes this query a poor health signal.

---

## 7. Health checks

```bash
curl -s http://localhost:8000/health
```

Returns `{"status":"ok"}` **without touching the database**, so it reports healthy during
a total database outage. For a real check, query any list endpoint:

```bash
curl -s http://localhost:8000/api/competitors | head -c 200
```

---

## 8. Running the checks

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

```bash
cd frontend && npx tsc --noEmit && npm run build
```

See `docs/TESTING.md` for what these do and do not cover.

---

## 9. Backups

There is no backup automation in this repository. Whatever managed PostgreSQL is in use
provides whatever it provides. Before any migration, before any `alembic stamp`, and
before any bulk `UPDATE` from this runbook:

```bash
pg_dump "$DATABASE_URL" > backup-$(date +%F-%H%M).sql
```

Store it outside the repository. Never commit a dump.
