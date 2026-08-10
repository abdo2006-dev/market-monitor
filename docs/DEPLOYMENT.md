# Deployment

Two topologies exist in this repository and they are **not** equivalent. Choosing between
them is an open decision — see `docs/adr/0006-background-jobs.md`.

---

## 1. Topology A — Docker Compose (complete architecture)

`docker-compose.yml`. Six services.

| Service | Image / build | Command | Port |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | — | 5432 |
| `redis` | `redis:7-alpine` | — | 6379 |
| `backend` | `./backend` | `alembic upgrade head && uvicorn app.main:app --reload` | 8000 |
| `celery_worker` | `./backend` | `celery -A app.workers.celery_app worker --concurrency=2` | — |
| `celery_beat` | `./backend` | `celery -A app.workers.celery_app beat` | — |
| `frontend` | `./frontend` | nginx (`frontend/nginx.conf`) | 3000→80 |

`postgres` and `redis` have healthchecks; backend, worker, and beat all wait on
`service_healthy`.

```bash
docker compose up --build
```

Frontend at `http://localhost:3000`, API at `http://localhost:8000`, docs at
`http://localhost:8000/docs`.

Notes:

- The backend service is the **only** place migrations are ever applied. If you run the
  worker without the backend, the schema is whatever was there before.
- `--reload` is on: this is a development configuration, not a production one.
- Both backend and worker mount `./backend:/app`, so container code is host code.
- `celery_beat` is given no `USER_AGENT`, `PLAYWRIGHT_HEADLESS`, or Discord settings —
  harmless today because beat only enqueues, but it will matter if beat ever executes.

### Scheduling in Topology A

Celery beat, `workers/celery_app.py:26`:

- `check-scan-schedule` → `check_and_schedule_scans` every **60 seconds**. Enqueues a scan
  for any active competitor whose `last_scan_at` is older than its
  `scan_frequency_minutes` and which has no run marked `running` within 30 minutes.
- `send-daily-summary` → every **86400 seconds from beat start**. This is an interval, not
  a wall-clock time; restarting beat moves the daily summary. `DAILY_SUMMARY_TIME` is not
  consulted.

---

## 2. Topology B — Vercel (what is actually deployed)

`vercel.json`.

```jsonc
{
  "experimentalServices": {
    "api": { "entrypoint": "backend/main.py", "routePrefix": "/api",
             "memory": 2048, "maxDuration": 300, "includeFiles": "backend/app/**" },
    "web": { "entrypoint": "frontend", "routePrefix": "/" }
  },
  "crons": [ { "path": "/api/cron/daily", "schedule": "0 8 * * *" } ]
}
```

`backend/main.py` is an ASGI wrapper around the FastAPI app that rewrites incoming paths
to prepend `/api` for anything that is not `/health`:

```python
if path and path != "/health" and not path.startswith("/api"):
    scope["path"] = f"/api{path}"
```

This exists to reconcile Vercel's route prefixing with the routers' own `/api` prefixes.
It is not exercised in local development, so routing behaviour differs between
environments.

### What Topology B does not have

- **No Celery worker.** Any code path calling `.delay()` enqueues to Redis with no
  consumer. The task never runs; the API still returns a `task_id`.
- **No Celery beat.** Scheduling depends entirely on the Vercel cron.
- **No migration step.** Nothing runs `alembic upgrade head`. The schema is whatever
  `init_db()`'s `create_all` produced on first boot — with no `alembic_version` stamp.
  See `docs/ARCHITECTURE.md` A-1; this is the most dangerous consequence of this topology.
- **No Playwright browser.** The serverless bundle does not install Chromium, so any
  competitor using `generic_selector` with listing URLs cannot be scraped at all.

### Scheduling in Topology B

One cron, `0 8 * * *` UTC → `GET /api/cron/daily`:

```
_check_auth(authorization)          # no-op unless CRON_SECRET is set
→ scan_due(...)                     # sequential, inline, per due competitor
→ _send_daily_summary_async()
```

All due competitors are scanned **sequentially inside a single 300-second invocation**.
With ~6 competitors and Shopify catalogues of a few hundred products this currently fits;
it will not scale, and there is no partial-progress recovery — a timeout loses the tail of
the list with no record of which competitors were missed.

### Consequences of the split

| | Compose | Vercel |
|---|---|---|
| Queued scans | run | **silently never run** |
| Scan cadence | per-competitor, minute granularity | once daily, all-at-once |
| Playwright scraping | works | **impossible** |
| Migrations | applied on deploy | **never applied** |
| Long scans | 900 s task limit | 300 s hard request limit |

This is the largest dev/prod divergence in the project. Any Phase 1 work on the scan
pathway must decide which topology is authoritative.

---

## 3. Environment variables

Template: `.env.example` (safe to read; contains no real values). Real values live in
`.env` / `.env.local`, both gitignored. **Never commit them.**

| Variable | Default | Actually used? |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://market:market@localhost:5432/market_monitor` | yes |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | yes |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | yes |
| `USER_AGENT` | a Chrome UA string | yes — but overridden for Shopify (`scraper.py:277`) |
| `PLAYWRIGHT_HEADLESS` | `true` | yes |
| `DEFAULT_MAX_PAGES` | `5` | yes |
| `DEFAULT_PAGE_DELAY_SECONDS` | `2.0` | yes |
| `DISCORD_NOTIFICATIONS_ENABLED` | `true` | yes |
| `DISCORD_DEFAULT_WEBHOOK_URL` | `None` | yes |
| `RUN_SCANS_INLINE` | `false` | yes |
| `CRON_SECRET` | `None` | yes — **but auth is skipped entirely when unset** |
| `SECRET_KEY` | `change-me-in-production` | **no** |
| `REDIS_URL` | `redis://localhost:6379/0` | **no** |
| `DEFAULT_TIMEZONE` / `DEFAULT_CURRENCY` | `UTC` / `USD` | **no** |
| `DEFAULT_SCAN_INTERVAL_MINUTES` | `60` | **no** |
| `MIN_PRICE_CHANGE_AMOUNT` / `MIN_PRICE_CHANGE_PERCENTAGE` | `0.01` / `0.1` | **no** |
| `IGNORE_KEYWORDS` | `""` | **no** |
| `DAILY_SUMMARY_ENABLED` / `DAILY_SUMMARY_TIME` | `true` / `08:00` | **no** |

Nine settings are inert. Setting them changes nothing. They are documented as live in
`.env.example`, which is misleading; correcting that file is Phase 1 work.

### `.env` loading

`config.py:29` sets `env_file = ".env"`, resolved relative to the **working directory**.
The only `.env` is at the repository root, and the backend runs from `backend/`. So:

- Running `uvicorn app.main:app` from `backend/` → **`.env` is not loaded**, class
  defaults apply.
- Docker Compose → env vars are injected directly by the compose file, so `.env` is
  irrelevant there.
- Vercel → env vars come from the project settings.

If a local setting appears to have no effect, this is why.

### `DATABASE_URL` normalization

`database.py:9` accepts `postgres://` and `postgresql://` and rewrites both to
`postgresql+asyncpg://`. It strips `sslmode` and `channel_binding` query parameters and
converts `sslmode` into `connect_args={"ssl": True}` — asyncpg does not accept libpq-style
parameters. This makes managed-Postgres URLs (Neon, Supabase, Heroku) work as pasted.

`poolclass=NullPool` (`database.py:31`): every session opens a new connection. Correct for
serverless, wasteful under Compose, and it means concurrent scans consume connections
linearly with no ceiling.

---

## 4. Deploying

### Compose

```bash
docker compose up --build -d
```

Migrations run automatically in the `backend` service's command.

### Vercel

Push to the connected branch. **Migrations do not run.** Before any deploy that includes a
migration, apply it manually against the production database:

```bash
cd backend && DATABASE_URL="<production-url>" .venv/bin/python -m alembic upgrade head
```

Confirm the database has an `alembic_version` table first — a `create_all`-built database
has none, and running `upgrade head` against it will fail. See `docs/RUNBOOK.md` §2.

---

## 5. Known deployment risks

1. **The production schema is probably unstamped** (A-1). Verify before the first
   migration-bearing deploy.
2. **Queued scans vanish on Vercel** (A-11). Any competitor not typed `shopify_json` or
   `salla_json` is effectively unmonitored there.
3. **The frontend build passes with type errors** — Vite does not typecheck. Adding
   `tsc --noEmit` to CI (Phase 0) closes this.
4. **CORS is `allow_origins=["*"]` with credentials** (`app/main.py:19`). See
   `docs/SECURITY.md`.
5. **Cron endpoints are public unless `CRON_SECRET` is set.** Set it in production.
6. **No healthcheck depth.** `/health` returns `{"status":"ok"}` without touching the
   database, so it reports healthy during a total database outage.
7. **687 KB single JS chunk**, no code splitting.
