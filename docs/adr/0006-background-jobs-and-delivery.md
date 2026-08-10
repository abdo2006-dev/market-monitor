# ADR 0006 — Background job execution and reliable notification delivery

**Status:** Partially accepted — the delivery mechanism is **Accepted**; the deployment
topology is **Open** and must be decided before Phase 1 scan work begins.

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

## Decision — part 2: execution topology (OPEN)

**This is not decided.** Recording the options so the next phase does not re-derive them.

**Option A — commit to a worker host** (Compose on a VPS, Fly.io, Render, Railway).
*For:* real Celery worker and beat; Playwright works; long scrapes fit; migrations run on
deploy; the outbox worker has a natural home; matches what the code already assumes.
*Against:* a running server to maintain and pay for; more operational surface than the
current serverless setup.

**Option B — commit to serverless** (stay on Vercel).
*For:* zero idle cost; already deployed and working for the JSON scrapers; no server to
maintain. *Against:* no Playwright, so generic-selector competitors are permanently
unscrapeable; all work must fit 300 seconds; the queue must be drained inline by the cron
in resumable chunks; the outbox needs a cron-driven drain; migrations remain a manual step.

**Option C — hybrid**: serverless API and UI, plus one small worker process elsewhere.
*For:* keeps the fast frontend deploy while restoring real background execution.
*Against:* two deployment targets, two sets of environment variables, two places to look
when something breaks.

### Why this must be decided before Phase 1 scan work

ADR 0003 specifies "create a durable run, then enqueue" as the universal pathway, with the
*runner* as a deployment concern. That design holds under all three options — but the
runner implementation differs substantially. Building the chunked, resumable inline drain
that Option B requires is significant work that Option A does not need at all. Choosing
after the fact means building the wrong one.

**Recommendation:** Option A. Every capability the application already assumes — Celery,
beat, Playwright, migrations on deploy, a long-running outbox drain — exists there for
free, and the current Vercel deployment is silently dropping queued scans today. But this
is a cost and preference decision for the project owner, not a technical conclusion, and it
is recorded here as open rather than assumed.

## Status summary

| Element | Status |
|---|---|
| Transactional outbox for notifications | **Accepted** |
| `events.scrape_run_id` | **Accepted** |
| Delivery is at-least-once with an idempotency key | **Accepted** |
| Backoff + dead-lettering | **Accepted** |
| Deployment topology (A / B / C) | **Open — decide before Phase 1 scan work** |
