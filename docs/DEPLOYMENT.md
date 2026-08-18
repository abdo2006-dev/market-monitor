# Deployment

Phase 1B.2 separates durable Sync business state from its execution provider. PostgreSQL
owns requests, claims, leases, retries, completeness, and outcomes. GitHub Actions is the
initial personal-use runner; Railway is the compatible reliability upgrade. Vercel remains
the web/API host and Docker Compose remains the complete local development topology.

No production deployment or database migration is automatic.

## 1. Initial production topology

```text
Browser ───────> Vercel frontend/FastAPI ───────> PostgreSQL
                         │                            ▲
                         └─ optional dispatch         │
                                   │                 │
                                   v                 │
                         GitHub Actions runner ───────┘
                         (same Sync worker CLI)
```

Manual API flow commits a durable request first, then optionally calls GitHub's workflow
dispatch API. Scheduled GitHub jobs create/reuse the deterministic Cairo morning request
and drain eligible work. A dispatch outage therefore changes only dispatch metadata; it
cannot lose or falsely complete the job.

Automatic request creation is a separate, default-off release gate. The Vercel
compatibility cron, provider-neutral worker `--morning` mode, and GitHub schedule all
require `SYNC_MORNING_ENABLED=true` in their own deployment environment. Manual V2
request creation and request-ID processing do not require it.

Live Export remains synchronous on the Vercel request path. Discord notification delivery
remains legacy and does not determine Sync success.

## 2. GitHub Actions Sync runner

Workflow: `.github/workflows/sync-v2.yml`.

### Manual

`workflow_dispatch` accepts one `request_id` UUID created by Market Monitor. It does not
accept a database URL, storefront URL, token, branch, or command. The API dispatcher is
optional (`SYNC_DISPATCH_PROVIDER=none` by default).

GitHub only accepts `workflow_dispatch` when the workflow file exists on the repository's
default branch. Scheduled workflows likewise exist and execute only from the latest
default-branch commit. Consequently this feature branch is validation-only: neither
production dispatch nor the schedules are available until the reviewed workflow is merged
to `main`. Do not add a PR event or `pull_request_target` to bypass that gate.

### Automatic recovery schedule

Two deliberately off-hour Cairo-local schedules run daily:

- `17 7 * * *`, `timezone: Africa/Cairo` — **07:17 Cairo**;
- `47 8 * * *`, `timezone: Africa/Cairo` — **08:47 Cairo**.

Change these two `cron` expressions in the workflow if the owner's morning window moves.
Do not replace the IANA timezone with a fixed UTC offset or remove daily idempotency. Both invocations use
`automatic:<Africa/Cairo local date>` and identical per-competitor keys, so the second is a
recovery opportunity, not a duplicate scan.

### `production-sync` environment

GitHub environment controls are separate and should be configured deliberately:

- **Environment secret:** store `PRODUCTION_DATABASE_URL` in `production-sync`, not as a
  frontend variable and preferably not as a repository-wide secret. GitHub exposes it only
  to the Sync and manual migration jobs after the environment gate passes.
- **Deployment branch restriction:** allow only the protected/default `main` branch. This
  complements the workflow event guard and trusted checkout.
- **Approval/protection rules:** do not configure required reviewers for routine automatic
  morning Sync in this personal-use deployment. A required reviewer pauses every scheduled
  job before the secret is released. Add one only if the owner explicitly prefers manual
  approval over unattended morning freshness.
- **Repository Actions variable:** leave `SYNC_MORNING_ENABLED` absent/false through the
  one-competitor and manual Sync-All proof. Change it to `true` only as the explicit
  Stage D action. It is not a secret; repository scope makes it available to the job-level
  schedule guard before the protected environment starts.

The environment does not grant repository permissions. The workflow still declares only
`contents: read`; environment protection controls eligibility and secret release.

The manual database workflow uses the same environment, accepts only its fixed
inspect/upgrade/stamp-head choices, refuses non-`main` refs, and checks out pinned trusted
`main` code. Its default remains the read-only classifier; write actions still require the
literal `MIGRATE` confirmation.

### Public-repository threat model

- No PR event can run the secret-bearing job; there is no `pull_request_target`.
- Manual work is guarded to `refs/heads/main`; checkout explicitly selects trusted `main`.
- `actions/checkout` and `actions/setup-python` are pinned to commit SHAs.
- Workflow permission is only `contents: read`; checkout credentials are not persisted.
- `PRODUCTION_DATABASE_URL` belongs in the protected `production-sync` environment.
- The optional dispatcher token is server-only, fine-grained, limited to this repository,
  and requires Actions write permission only.
- Shell/debug tracing must not be enabled and worker logs contain only safe IDs, counts,
  states, categories, and timestamps.

GitHub scheduled execution remains best-effort. The two opportunities, durable leases,
and future drains recover state, but cannot guarantee exact start time.

The shared CLI emits JSON lines with non-secret identifiers/state. Exit codes are `0` for
completed/no eligible work, `1` for terminal or unexpected failure, `2` when durable retry
is waiting, and `3` for invalid/unavailable requested work. PostgreSQL status remains the
authoritative diagnosis even when the provider marks a job failed.

## 3. Railway upgrade

Deploy the same backend code and dependency set with the same PostgreSQL connection and
run `python -m app.workers.sync_worker` repeatedly as a persistent service. A small service
loop can invoke `--once`/`--drain`; the CLI and all application logic are unchanged.

Configure at minimum:

```text
DATABASE_URL=<managed PostgreSQL async URL>
SYNC_EXECUTION_MODE=v2
DB_SCHEMA_CHECK=strict
```

Provider migration procedure:

1. keep GitHub scheduled/manual execution active while Railway is deployed in an explicit
   test window;
2. verify Railway claims, heartbeats, terminal results, and retries in PostgreSQL;
3. stop API GitHub dispatch and GitHub schedules;
4. confirm the Railway worker drains an intentionally queued request;
5. retain the same status API/UI and database schema.

Only provider configuration and process supervision change. Request, acquisition,
reconciliation, completeness, retry, and freshness code do not.

## 4. Docker Compose and legacy coexistence

`docker-compose.yml` still contains PostgreSQL, Redis, backend, Celery worker/beat, and the
frontend. This is useful for development and notification compatibility.

With `SYNC_EXECUTION_MODE=v2`:

- V2 manual and cron routes create PostgreSQL jobs;
- `sync_worker.py` consumes them;
- the old every-minute Celery scan scheduler is disabled;
- legacy Celery scan tasks remain callable only as rollback code;
- Redis is not a Sync source of truth.

With `SYNC_EXECUTION_MODE=legacy`, the old inline/Celery branches are restored. Do not run
V2 workers and create legacy requests for the same intended scan. Remove Celery/Redis Sync
code only after explicit production proof; notification cleanup is separate work.

## 5. Environment variables

Safe template: `.env.example`. Never commit `.env`, tokens, database URLs, browser
profiles, or webhook URLs.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local PostgreSQL | API and worker durable state |
| `SYNC_EXECUTION_MODE` | `v2` | explicit durable/legacy boundary |
| `SYNC_MAX_ATTEMPTS` | `3` | durable run attempt budget |
| `SYNC_LEASE_SECONDS` | `600` | claim lease; heartbeat renews at most every 60s |
| `SYNC_RUNNER_WAIT_SECONDS` | `300` | age after which queued work is reported as waiting for runner/recovery |
| `SYNC_MORNING_ENABLED` | `false` | default-off automatic morning request/drain gate |
| `SYNC_DISPATCH_PROVIDER` | `none` | set `github_actions` only on the server |
| `GITHUB_ACTIONS_DISPATCH_TOKEN` | unset | fine-grained server-only Actions token |
| `GITHUB_ACTIONS_REPOSITORY` | unset | `owner/repository` dispatch target |
| `GITHUB_ACTIONS_WORKFLOW` | `sync-v2.yml` | workflow identifier |
| `GITHUB_ACTIONS_REF` | `main` | trusted dispatch ref |
| `CRON_SECRET` | unset | bearer protection for HTTP cron compatibility |
| `APP_AUTH_ENABLED` | `false` | must be `true` on public Production |
| `APP_AUTH_PASSWORD_HASH` | unset | PBKDF2 hash generated by `scripts/hash_auth_password.py` |
| `APP_AUTH_SESSION_SECRET` | unset | independent server-only signing secret, minimum 32 characters |
| `APP_AUTH_SESSION_TTL_SECONDS` | `43200` | signed browser session lifetime |
| `DB_SCHEMA_CHECK` | `warn` | change to `strict` only after production migration |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | local Redis | legacy/notification path only under V2 |
| scraper/Discord settings | see `.env.example` | existing adapter and notification configuration |

The GitHub workflow sets `DATABASE_URL` from `secrets.PRODUCTION_DATABASE_URL` in the
`production-sync` environment. It must not share the optional GitHub dispatcher token.

When the optional GitHub dispatcher is enabled, the Vercel server requires a separate
fine-grained token limited to this repository with **Actions: write**. The browser never
receives it. Dispatch failure leaves PostgreSQL work queued, records only a safe category,
and can be retried with `POST /api/sync/requests/{uuid}/dispatch`. The 07:17/08:47 Cairo
workflow remains the scheduled recovery path; the second invocation derives the same daily
idempotency key and cannot duplicate a successful first run.

## 6. Staged production deployment

Follow `docs/RUNBOOK.md` §2.2–2.4. Summary:

1. classify the database read-only;
2. back up and verify a restore plan;
3. resolve Case B/B-/C honestly; never stamp Case C;
4. audit/remediate product duplicates if needed;
5. migrate through `0004`, then `0005` using the manual database workflow;
6. verify head, schema constraints/indexes, and application startup;
7. merge the reviewed branch so `sync-v2.yml` exists on default `main`;
8. enable the single-user application access gate, then deploy API/UI with
   `SYNC_EXECUTION_MODE=v2`, dispatcher disabled, and `SYNC_MORNING_ENABLED=false`;
9. configure the protected GitHub environment and run one explicit request;
10. enable the optional dispatcher if desired;
11. set `DB_SCHEMA_CHECK=strict` only after proof;
12. enable the GitHub repository Actions morning variable only after manual Sync-All proof,
    while leaving Vercel's variable false.

Rollback application behavior with `SYNC_EXECUTION_MODE=legacy`. A schema downgrade exists
for controlled recovery, but do not downgrade production merely to toggle execution mode.
Never allow an old lease owner to keep running during rollback.

## 7. Known deployment risks

1. Production schema state is unknown until the owner classifies it.
2. GitHub schedules can be delayed/dropped and disabled after public-repository inactivity.
3. Manual GitHub runner latency is materially higher than a persistent worker.
4. Future-dated `retry_wait` work needs a later invocation; PostgreSQL preserves it but
   GitHub is not a continuously polling service.
5. Playwright was not required by the benchmark, but a future browser competitor increases
   runner install/runtime variance.
6. Keep Vercel `SYNC_MORNING_ENABLED=false`; GitHub is the selected automatic morning
   owner. Database idempotency remains a backstop, not permission to run both schedulers.
7. CORS/cron-auth/health-depth risks outside Sync remain documented in `docs/SECURITY.md`.
