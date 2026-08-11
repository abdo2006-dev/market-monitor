# ADR 0008 — Sync execution topology

**Status:** Proposed (2026-08-11; no topology implementation has begun)

## Context

Market Monitor has three different acquisition workloads and they do not need one
execution topology:

| Workload | Required behavior |
|---|---|
| Automatic Sync | Finish before morning use; asynchronous; durable status and retries matter more than exact start time. |
| Manual Sync Now | Return an acknowledgement immediately; acquisition may be asynchronous; the UI must poll durable progress and must not confuse acceptance with success. |
| Live Export | Return the requested file; it remains synchronous until Phase 1D deliberately introduces a prepare/download flow. |

The deployed Vercel application has no Celery worker or beat. Enqueuing to Redis can
therefore return a task ID for work that never runs. PostgreSQL is already the source of
truth and Phase 1B.2 must add a durable scan lifecycle regardless of the execution host.

### Measured acquisition workload

One normal sequential benchmark of all 12 active configured competitors was run on
2026-08-11 with the application pacing and five-page cap:

- 30.08 s measured wall time; 30.04 s sum of competitor durations;
- 1.99 s median; 8.23 s slowest competitor;
- 11 non-empty results, 7,950 priced products; one empty result that a real Sync rejects;
- 65 measured HTTP requests, 49 successful catalog-page requests, no 429 or 5xx response;
- 11 Shopify HTTP strategies and one Salla HTTP strategy; zero configured competitors
  required Playwright;
- maximum measured Python allocation peak 17.4 MB (not process RSS);
- no explicit application retries. Seven repeated GETs came from fallback/discovery paths.

This was acquisition-only. It does not include reconciliation, notifications, runner
startup, or dependency installation. The scraper contract cannot report completeness.
Three competitors returned exactly `5 * 250` products, so the page cap may have truncated
them. One run is evidence about shape and order of magnitude, not an SLA.

### Current provider facts

Facts were checked against official documentation on 2026-08-11:

- Vercel supports Python/FastAPI. Its Fluid Compute duration page documents a 300 s Hobby
  maximum, while the Hobby comparison page still says 60 s; the repository requests
  300 s. Either published limit contains this 30 s acquisition sample, but the conflict
  is itself an operational risk. Hobby cron is once daily with hourly precision
  (`±59 min`). Node/Edge document `waitUntil`; Python does not have an equivalent durable
  background guarantee. Python bundles are 500 MB on the standard path and Large
  Functions can package browser automation, but Large Functions are beta.
- Vercel Queues is beta on all plans and provides at-least-once delivery and retry. Its
  documented push examples/SDK are Node-oriented; poll mode still needs a consumer.
- Public repositories incur no GitHub-hosted Actions minutes. A hosted job may run for
  six hours and `workflow_dispatch` can be triggered through the REST API. Scheduled
  workflows can be delayed or dropped under load and are disabled after 60 days without
  public-repository activity. Playwright officially supports GitHub Actions.
- Railway has a $0 plan with $1 monthly credit, but an always-on worker is not realistically
  free. Hobby is a $5 monthly minimum including $5 of usage. A Dockerized Python worker is
  a normal persistent service.
- Render has no free background-worker instance. A scheduled cron service has a $1 monthly
  minimum, but it solves scheduled execution rather than low-latency interactive work.

Official references:

- [Vercel function duration](https://vercel.com/docs/functions/configuring-functions/duration)
- [Vercel Hobby limits](https://vercel.com/docs/plans/hobby)
- [Vercel cron pricing and precision](https://vercel.com/docs/cron-jobs/usage-and-pricing)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel Queues](https://vercel.com/docs/queues)
- [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)
- [GitHub workflow triggers](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [GitHub public-repository billing](https://docs.github.com/en/actions/how-tos/monitor-workflows/view-job-execution-time)
- [Railway plans](https://docs.railway.com/pricing/plans)
- [Render cron jobs](https://render.com/docs/cronjobs)
- [Playwright on CI](https://playwright.dev/docs/ci)

## Decision proposed

### Primary: persistent Railway worker, PostgreSQL-backed job queue

Use one small Dockerized Python worker on Railway. PostgreSQL `scrape_runs` and related
tables are the durable queue and execution record; Redis is not required for Sync.

```text
Automatic scheduler ─┐
                     ├─> RequestCompetitorScan ─> PostgreSQL queued run
User / Sync Now ─────┘                              │
                                                   v
                              Railway worker claim / bounded retry
                                                   │
                                                   v
                         scraper acquisition -> advisory-locked reconciliation
                                                   │
                                                   v
                                               PostgreSQL
```

- The worker checks due competitors and queued runs through application use cases, claims
  with an atomic database transition, and processes at bounded competitor-level
  concurrency (begin with one; the measured workload does not justify more).
- Manual Sync writes the durable request and returns `202` plus the run ID. The UI polls
  status. The worker's short polling interval gives interactive behavior without keeping
  a Vercel request open.
- Automatic Sync is created ahead of the owner's morning window. Exact minute precision
  is not a product requirement; completion status is.
- Failures record an attempt and non-secret category, retry with bounded backoff, and end
  in an explicit terminal state. A reaper recovers abandoned `running` claims.
- Search derives freshness from the newest complete successful run. Failed or partial
  attempts never advance the successful-observation watermark.
- Phase 1B.1's per-competitor advisory lock, committed-success ordering guard, and unique
  product constraints remain the final reconciliation defenses.

Live Export stays on the Vercel FastAPI request path:

```text
Browser -> Vercel FastAPI -> acquisition -> serialize -> file response
```

The full-catalog benchmark suggests adequate headroom, but Export needs its own timeout
and Phase 1D provenance. It must not silently fall back while claiming live data.

### Fallback: public GitHub Actions as an ephemeral worker ($0)

Use the same PostgreSQL lifecycle and processing command, but execute it on a hosted
Actions runner:

```text
GitHub schedule ────────────────┐
                               ├─> GitHub runner -> claim DB run -> scraper
Sync Now -> DB run -> dispatch ┘                         │
                                                        v
                                      advisory-locked reconciliation -> PostgreSQL
```

The FastAPI endpoint first commits the request, then triggers `workflow_dispatch`; the
dispatch response is never reported as scan success. A scheduled workflow requests due
runs and drains them. The workflow must exist only on the default branch, use repository
or environment secrets, have minimal permissions, and never run the secret-bearing path
for pull requests or forks.

This is viable for the measured 30 s acquisition and costs no Actions minutes while the
repository is public. It loses to the primary because runner queue/setup time hurts manual
latency, scheduled runs are best-effort, inactivity can disable the schedule, and logs
and observability are split between GitHub and the application.

## Decision matrix

Scores: 5 is best for Market Monitor; cost is the expected ongoing minimum.

| Option | Cost | Auto reliability | Manual latency | Browser | Retries/visibility | Ops | Fit | Total / 35 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Railway persistent worker + DB queue | ~$5/mo | 5 | 5 | 5 | 5 | 4 | 5 | **34** |
| GitHub Actions ephemeral worker + DB queue | $0 public repo | 3 | 2 | 5 | 4 | 3 | 4 | **26** |
| Vercel inline cron/functions | $0 Hobby | 3 | 2 | 3 | 2 | 5 | 4 | **22** |
| Vercel Queues + function consumer | $0 within Hobby caps | 4 | 5 | 3 | 4 | 3 | 2 | **25** |
| Local always-on worker + DB queue | $0 | 2 | 4 when awake | 5 | 4 | 2 | 4 | **21** |
| Render cron only | >=$1/mo | 4 scheduled | 1 | 5 | 3 | 4 | 3 | **20** |

Why the close alternatives lose:

- **Vercel inline:** duration is currently sufficient, but it does not provide immediate
  acknowledgement plus durable Python background execution. Function termination and
  retry still have to be rebuilt around PostgreSQL.
- **Vercel Queues:** technically promising and fast, but currently beta and Node-oriented;
  PostgreSQL must still own scan state. Adding a second queue is more integration work than
  this single-user system needs.
- **Local worker:** excellent for development and manual fallback, but morning reliability
  depends on the owner's computer, power, sleep state, and network.
- **Render cron:** inexpensive for automatic Sync, but not a consumer for interactive jobs.

## Celery and Redis

Replace Celery + Redis for Sync after the Phase 1B.2 path is proven. Do not remove the old
path first. A single-user application with PostgreSQL already needs durable run state; a
simple worker polling and atomically claiming that state is less infrastructure than
maintaining an additional broker whose absence currently causes silent loss.

Redis may remain for unrelated caching if a measured need exists. It must not be the
source of truth for scan requests, attempts, results, or notification delivery.

## Consequences and limitations

- The primary deliberately pays about $5/month for prompt manual execution and reliable
  morning processing. The fallback is genuinely $0 but best-effort.
- No topology fixes acquisition completeness. The observation contract must gain
  strategy, pages, completeness, and `observed_at` metadata.
- The worker and Vercel API connect to the same hosted PostgreSQL over TLS. Credentials
  stay in provider secrets; logs must never include URLs, tokens, or connection strings.
- One worker is enough now. Competitor-level concurrency starts at one and increases only
  after measured need and site-friendly rate limits.
- This ADR is Proposed. Phase 1B.2 must not start until the owner accepts either the
  primary or fallback and chooses the ongoing-cost tradeoff.

## Migration implications (not implemented here)

1. Add the durable lifecycle and database claim/retry fields.
2. Extract `RequestCompetitorScan` and `ProcessCompetitorScan` application use cases.
3. Add one runner CLI usable unchanged by Railway, GitHub Actions, and local development.
4. Deploy the selected runner in shadow/explicit-test mode.
5. Converge automatic and manual triggers on durable requests; add UI polling.
6. Only after production evidence, remove Celery scan tasks and Redis broker dependence.
7. Keep Live Export synchronous and handle provenance in Phase 1D.
