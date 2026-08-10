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

### 2.2 Recovering a database that was built by `create_all`

This is the expected state of the Vercel-deployed database (`docs/ARCHITECTURE.md` A-1).

1. Confirm the diagnosis:

```bash
psql "$DATABASE_URL" -c "SELECT count(*) FROM information_schema.tables WHERE table_name='alembic_version';"
```

`0` means unstamped.

2. Compare the live schema against `0002_product_category`. Both migrations are already
   reflected in the ORM models, so if the tables and columns are present, the database is
   logically at head — only the stamp is missing.

3. Stamp it, **without running any DDL**:

```bash
cd backend && .venv/bin/python -m alembic stamp 0002_product_category
```

4. Verify:

```bash
cd backend && .venv/bin/python -m alembic current
```

5. Expect index names to differ from a freshly-migrated database
   (`ix_product_snapshots_*` instead of `ix_snapshots_*`). Do not rename them ad hoc — a
   future migration must handle both spellings with `IF EXISTS`.

**Take a backup before stamping.** Stamping the wrong revision silently skips migrations.

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

## 4. Clearing stuck `running` scrape runs

A crashed scan leaves a permanent `running` row. Eligibility checks ignore rows older than
30 minutes, so scanning resumes on its own — but the rows accumulate and skew any query
over run status. There is no reaper.

Inspect:

```sql
SELECT id, competitor_id, started_at, now() - started_at AS age
FROM scrape_runs WHERE status = 'running' AND started_at < now() - interval '1 hour';
```

Reconcile (after confirming no worker is actually running them):

```sql
UPDATE scrape_runs
SET status = 'failed', finished_at = now(), error_message = 'Abandoned: reconciled manually'
WHERE status = 'running' AND started_at < now() - interval '1 hour';
```

---

## 5. "I pressed Scan and nothing happened"

Decision path:

1. **What is the competitor's `scrape_type`?**
   - `shopify_json` or `salla_json` → the scan ran **inline** in the HTTP request. A
     `scrape_runs` row exists. Go to §3.
   - anything else → the scan was queued to Celery. Continue.

2. **Is a Celery worker running?**

```bash
cd backend && .venv/bin/python -m celery -A app.workers.celery_app inspect active
```

   - No reply → no worker. On Vercel there is **never** a worker
     (`docs/DEPLOYMENT.md` §2), so queued scans never run and never will. The API returned
     a `task_id` regardless. This is risk A-2/A-11, not a misconfiguration you can fix
     without changing topology.

3. **Is Redis reachable?** The broker URL is `CELERY_BROKER_URL`.

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
