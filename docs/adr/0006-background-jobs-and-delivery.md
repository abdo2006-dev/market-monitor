# ADR 0006 — Background job execution and reliable notification delivery

**Status:** Partially accepted — the delivery mechanism is **Accepted**; the deployment
topology proposal continues in ADR 0008.

## Context

### Notification delivery is unreliable today

`workers/tasks.py:93-124` runs after a scan commits:

```python
SELECT Event WHERE competitor_id = ? AND notification_sent = false   # NOT scoped to this run
→ send Discord webhooks
→ set event.notification_sent = True                                 # in memory only
→ COMMIT                                                             # afterwards
```

Two compounding defects:

1. **No run scoping.** `events` has no `scrape_run_id` column, so "events from this scan"
   is inexpressible. Two overlapping scans of one competitor each pick up the other's
   events and both send them.
2. **Send-then-persist.** A crash, a deploy, or Celery's 900-second `task_time_limit`
   between the webhook POST and the commit re-sends every already-delivered message on the
   next scan.

Additionally: delivery is serial with a hardcoded `asyncio.sleep(1.0)` per message, so 100
events block for 100+ seconds; a 429 response sleeps and then **drops** the message
(`notification.py:25-29`); and `scrape_failed` events are never marked sent because
`dispatch_event_notifications` has no branch for them, so they accumulate permanently.

### Job execution topology is unresolved

Two deployments exist with materially different capabilities:

| | Docker Compose | Vercel (deployed) |
|---|---|---|
| Celery worker | yes, concurrency 2 | **absent** |
| Celery beat | yes, 60 s tick | **absent** |
| Migrations on deploy | yes | **absent** |
| Playwright browser | yes | **absent** |
| Scheduling | per-competitor cadence | one daily cron |
| Time budget | 900 s per task | 300 s per request |

On Vercel, any `.delay()` call enqueues to Redis with no consumer. The scan never runs; the
API returns a `task_id` anyway.

## Decision — part 1: transactional outbox (Accepted)

Notifications are delivered through a **transactional outbox**.

```
ProcessCompetitorScan
  └─ [ONE TX] products + snapshots + events + outbox rows + run status

DeliverNotifications  (separate worker loop)
  └─ [TX] claim N rows: SELECT ... FOR UPDATE SKIP LOCKED
          → Notifier.send(entry)          idempotency key = outbox row id
          → delivered | attempts+1 with backoff | dead-letter after N attempts
```

Specifics:

- New `outbox_entries` table: `(id, event_id, channel, status, attempts, last_error,
  available_at, delivered_at)`.
- `events.scrape_run_id` FK added, so events are attributable to a run.
- `notification_sent` / `notification_sent_at` move off `Event`. `Event` becomes purely an
  audit record; the outbox is the delivery queue. These are currently one table doing two
  jobs.
- Delivery is **at-least-once**: an event is durably recorded before any webhook is
  attempted. Exactly-once is not achievable against a webhook with no idempotency support,
  and pretending otherwise would be dishonest. The idempotency key makes duplicates
  detectable and rare rather than routine.
- Failure notifications go through the outbox too, so they finally obey
  `DISCORD_NOTIFICATIONS_ENABLED` — today they bypass it.
- Real backoff replaces the fixed 1-second sleep; 429 becomes a retry with the advertised
  `retry_after`, not a drop.

### Alternatives considered

**Keep the current flag on `Event`, just scope the query by `scrape_run_id`.** Cheaper, and
it fixes defect 1. Rejected because it leaves defect 2 — the send-then-persist window —
entirely intact, which is the one that causes duplicate deliveries in production.

**Send notifications from the same transaction (two-phase-ish).** Not possible: an HTTP
POST cannot participate in a database transaction. Holding the transaction open across the
webhook call would make it worse, not better.

**Use Celery's retry mechanism for notifications.** Rejected: it moves delivery state into
the broker, so a Redis flush loses the record of what was and was not sent. PostgreSQL is
the source of truth (ADR 0002).

**A dedicated queue product (RabbitMQ, SQS).** Rejected as overengineering. The outbox is
~150 lines and one table.

### Consequences

*Positive:* duplicate notifications become rare and detectable; failed deliveries are
retried with backoff instead of dropped or retried unboundedly; delivery is decoupled from
scan latency, so a slow Discord endpoint no longer extends a scan; a dead-letter state
makes persistent failures visible.

*Negative:* one new table, one migration, one new worker loop; `notification_sent` moving
off `Event` is a breaking change for anything reading it (currently only
`GET /api/events?notification_sent=`); at-least-once means the operator may still
occasionally see a duplicate message, and that expectation must be documented rather than
denied.

## Decision — part 2: execution topology (continued by ADR 0008)

> This section is the Phase 0, pre-benchmark analysis. ADR 0008 contains the measured
> 2026-08-11 workload, refreshed provider facts, and current Proposed decision. Preserve
> this section as the historical reasoning; do not use it as current operational advice.

**Still not decided as of Phase 1A.** Phase 1A added the evidence-gathering tool
(`backend/scripts/benchmark_scan.py`) but the owner has not yet run it against their real
competitors, so the numbers that settle this do not exist. See §"Missing evidence" below.

### 2.1 Separate the three workloads

They do **not** need the same execution topology, and treating them as one problem is why
the decision has been hard:

| Workload | Trigger | Latency need | Duration | Can it be queued? |
|---|---|---|---|---|
| **Interactive Sync** ("Scan" button) | human, on demand | user is watching; needs feedback in seconds | one competitor | yes — if the UI polls |
| **Scheduled Sync** ("scan all", nightly) | cron / beat | nobody watching | all competitors, serial cost | yes, and should be |
| **Live Export** | human, on demand | user is waiting for a file download | one collection | **no** — the response *is* the file |

Live Export is the constraint that rules out a pure queue-everything design: the browser
navigates to the URL and expects bytes back. It must stay synchronous (or become a
two-step "prepare then download", which is a UX change, not just a topology one).

### 2.2 Decision matrix

Scoring is against this application's actual needs: one operator, daily use, reliability
over elegance, no paid infrastructure requested.

| Criterion | A — persistent worker | B — serverless only (today) | C — hybrid (serverless + free runner) |
|---|---|---|---|
| Queued scans actually run | ✅ yes | ❌ **no consumer; silently dropped** | ✅ yes |
| Playwright / browser strategies | ✅ works | ❌ impossible (no Chromium in bundle) | ✅ works on the runner |
| Long scrapes | ✅ 600 s soft limit, tunable | ❌ hard 300 s per request | ✅ on the runner |
| Scheduled sync granularity | ✅ per-competitor cadence, 60 s tick | ⚠️ one cron/day (Vercel Hobby) | ⚠️ depends on runner (GH Actions ~5 min floor) |
| Migrations on deploy | ✅ natural | ❌ manual (Phase 1A adds a manual workflow) | ⚠️ runner can own it |
| Outbox drain (ADR 0006 part 1) | ✅ continuous loop | ⚠️ cron-driven, adds latency | ✅ continuous on runner |
| Interactive Sync feedback | ✅ enqueue + poll | ⚠️ inline only, bounded by 300 s | ✅ enqueue + poll |
| Live Export | ✅ fine | ⚠️ fine today, 300 s ceiling | ✅ fine (stays on serverless) |
| Failure recovery | ✅ retry, ack-late, reaper | ❌ a timeout loses the tail with no record | ✅ retry on runner |
| Operational complexity | ⚠️ one server to keep alive | ✅ lowest | ❌ **highest** — two targets, two env sets |
| Cost | ❌ ~$5–7/mo realistically | ✅ free | ✅ free-ish |
| Observability | ✅ one place | ⚠️ per-invocation logs | ❌ split across two |
| Failure is *visible* | ✅ | ❌ **worst property: silent** | ✅ |

**Free-tier realities, honestly stated:** Render and Fly.io free tiers sleep or have been
withdrawn for always-on workers; Railway's free allowance is trial-only. A genuinely free
persistent worker is not reliably available in 2026. Option C's "free compute" is usually
GitHub Actions on a schedule — which is real and workable, but its cron is best-effort
(delays of 5–30 minutes under load are normal), it is not designed as a job runner, and
long-running scheduled Actions on private repos consume included minutes.

### 2.3 Recommendation

**Option A, with Live Export staying synchronous.**

Reasoning, in priority order:

1. **The current topology fails silently.** Option B's defining property is that a queued
   scan returns a `task_id` and never runs. For an application whose entire value is
   "is my price data current?", a failure mode that looks like success is the worst
   possible one. Everything else in this matrix is secondary to that.
2. **Every capability the code already assumes exists in A for free** — Celery, beat,
   Playwright, migrations on deploy, a continuous outbox drain. Option B requires building
   a chunked, resumable, cron-driven queue drain that A does not need at all.
3. **Reliability is the stated Phase 1 objective**: "make Sync reliable enough that Search
   can be trusted every morning." Option B cannot deliver that for browser-based
   competitors at any amount of engineering effort, because there is no browser.
4. Cost is roughly $5–7/month. The instruction was not to choose something *solely*
   because it is free; this is the case where paying a small amount buys the property that
   matters most.

Option C is the fallback if paid hosting is genuinely unacceptable. It gets most of A's
reliability at zero marginal cost, at the price of two deployment targets and the worst
observability story of the three. If C is chosen, the runner should own scheduled sync and
the outbox drain, while interactive Sync and Live Export stay on serverless.

Option B should be rejected regardless of cost preference, unless the owner accepts
permanently abandoning browser-based competitors and one-scan-per-day granularity.

### 2.4 Missing evidence — what would change this

The recommendation above is based on capability, not on measured load. Run:

```bash
cd backend && .venv/bin/python scripts/benchmark_scan.py --json /tmp/bench.json
```

Then reconsider if:

- **Sum of durations is comfortably under ~200 s** and **no competitor needs a browser** —
  Option B becomes genuinely viable for scheduled sync, and the only remaining objection is
  once-daily granularity.
- **Any competitor exceeds ~120 s alone** — Option B is dead even for a single interactive
  scan, because a serial scan-all cannot fit 300 s.
- **Any competitor requires Playwright** — Option B is already dead for that competitor.

Record the measured numbers in `docs/DAILY_CRITICAL_WORKFLOWS.md` §6 and update this ADR to
`Accepted` with the chosen option.

### Why this must be decided before the Phase 1B scan migration

ADR 0003 specifies "create a durable run, then enqueue" as the universal pathway, with the
*runner* as a deployment concern. That design holds under all three options — but the
runner implementation differs substantially. Building the chunked, resumable inline drain
that Option B requires is significant work that Option A does not need at all. Choosing
after the fact means building the wrong one.

## Status summary

| Element | Status |
|---|---|
| Transactional outbox for notifications | **Accepted** |
| `events.scrape_run_id` | **Accepted** |
| Delivery is at-least-once with an idempotency key | **Accepted** |
| Backoff + dead-lettering | **Accepted** |
| Deployment topology (A / B / C) | **Open — decide before Phase 1 scan work** |
