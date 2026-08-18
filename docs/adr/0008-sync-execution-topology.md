# ADR 0008 — Sync execution topology

**Status:** Accepted (2026-08-11; implemented by Phase 1B.2)

## Context

Market Monitor needs durable automatic morning Sync and truthful asynchronous manual Sync.
The Vercel API process cannot be treated as a durable Python background worker, and the
deployed topology has no guaranteed Celery consumer. PostgreSQL is already the durable
source of truth.

The Phase 1B.1 acquisition-only benchmark completed 12 active competitors sequentially in
30.08 seconds (1.99-second median, 8.23-second slowest). Eleven were non-empty, no configured
competitor required Playwright, and the maximum measured Python allocation peak was 17.4
MB. Three Shopify stores returned exactly `5 × 250 = 1,250` products, so execution hosting
and acquisition completeness must remain separate concerns.

GitHub-hosted Actions is currently a practical zero-cost personal-use provider for a
public repository, but its schedule is best-effort and runner startup adds manual latency.
Railway provides a normal persistent worker deployment with an ongoing cost. Neither
provider should own business state or reconciliation policy.

Live Export remains synchronous. This decision concerns competitor Sync only.

## Decision

### Durable architecture: PostgreSQL jobs plus a provider-neutral worker

`SyncRequest`, `ScrapeRun`, and their association are the durable request, queue, attempt,
lease, outcome, and status model. A single worker CLI claims with PostgreSQL, runs catalog
acquisition outside a database transaction, then invokes the same application-level
reconciliation use case regardless of hosting provider.

```text
API / scheduler -> Request*Scan -> PostgreSQL queued run
                                      |
                         worker claim + committed lease
                                      |
                             external acquisition
                                      |
                 advisory-locked reconciliation + terminal state
                                      |
                                  PostgreSQL
```

GitHub Actions, Railway, and local execution all use:

```bash
python -m app.workers.sync_worker
```

The processing use case contains no GitHub or Railway assumptions. PostgreSQL, not Redis
or a provider queue, enforces non-terminal uniqueness, atomic claiming, fencing, retries,
and recovery.

### Initial personal-use deployment: GitHub Actions

The initial provider is `.github/workflows/sync-v2.yml`:

- `workflow_dispatch` accepts only a durable request UUID;
- two off-hour schedule entries run at 07:17 and 08:47 with `timezone: Africa/Cairo`, so
  daylight-saving changes are handled by GitHub rather than fixed UTC offsets;
- both schedules derive the same Cairo local-date idempotency key, so the second means
  “ensure/recover today” rather than “scan again”;
- scheduled execution is rollout-gated by the repository Actions variable
  `SYNC_MORNING_ENABLED`, which defaults false through manual production proof;
- each runner recovers expired leases and drains eligible PostgreSQL work;
- the API may optionally dispatch after it commits the request. Dispatch failure leaves
  the durable request queued and visibly records only a safe category.

Security for the public repository:

- there is no `pull_request` or `pull_request_target` secret-bearing path;
- the job guard and checkout force trusted `main` code;
- external actions are pinned to commit SHAs and checkout credentials are not persisted;
- permissions are `contents: read` only;
- the database secret lives in the protected `production-sync` GitHub environment;
- the environment restricts deployment to `main` but has no required reviewer for routine
  scheduled work unless the owner intentionally accepts approval-gated mornings;
- an optional fine-grained dispatcher token stays server-side, targets only this
  repository, and needs Actions write permission only;
- workflow inputs cannot supply URLs, database credentials, or shell fragments.

GitHub Actions is an execution provider, not a domain dependency. Schedule delays,
inactivity disablement, runner setup latency, and a `retry_wait` job awaiting a later
invocation are accepted initial trade-offs.

### Professional reliability upgrade: Railway persistent worker

The upgrade is to deploy the same repository image/requirements and the same worker CLI as
a persistent Railway process connected to the same migrated PostgreSQL database. Configure
`DATABASE_URL`, `SYNC_EXECUTION_MODE=v2`, and the normal scraper settings; run repeated
`--once`/`--drain` invocations or a thin service loop around the CLI.

No domain, API, acquisition, reconciliation, schema, or status-contract rewrite is needed.
Disable GitHub manual dispatch and scheduled execution only after the Railway worker has
proved claims, leases, retries, and morning completion in production. The gain is prompt
manual pickup and continuous retry/recovery; the trade-off is ongoing hosting cost.

## Alternatives considered

- **Railway first:** operationally stronger, but the measured personal workload does not
  initially justify its recurring cost. It remains the deliberate upgrade path.
- **Vercel inline/background execution:** cannot make a Python request process the durable
  owner after returning HTTP 202 and still requires PostgreSQL recovery semantics.
- **Vercel Queues:** adds a second queue while PostgreSQL must still own job truth; also
  increases provider coupling for this small workload.
- **Celery + Redis:** retained for rollback/notifications, but the current deployment has
  no guaranteed consumer and Redis must not own durable Sync requests.
- **Local always-on worker:** useful for development but morning reliability depends on a
  personal machine's power, sleep, and network state.
- **Render cron:** can schedule work but does not solve low-latency manual consumption.

## Consequences

- Manual endpoints return HTTP 202 and the UI polls PostgreSQL-backed status; acceptance or
  dispatch is never reported as completion.
- Two workers cannot legitimately claim the same lease. UUID fencing prevents an expired
  owner from reconciling after a new owner takes over.
- The worker holds no transaction during website I/O. The Phase 1B.1 competitor advisory
  lock remains the final reconciliation serialization boundary.
- Complete/partial/suspicious/failed acquisition evidence is first-class and independent
  from execution status. A fifth full Shopify page is partial, not proof of coverage.
- Celery and Redis remain available under `SYNC_EXECUTION_MODE=legacy` and for notification
  compatibility. The V2 beat scan scheduler is disabled to prevent dual execution.
- Production adoption requires the staged schema classification/migration and explicit
  proof in `docs/RUNBOOK.md`; this ADR does not authorize automatic deployment or writes.
- GitHub is the one production automatic owner after proof. Vercel keeps its separate
  `SYNC_MORNING_ENABLED` value false so the compatibility cron cannot start a second
  morning invocation.
