# ADR 0003 — One authoritative scan execution pathway

**Status:** Accepted (Phase 0, 2026-08-10)

## Context

"Scan a competitor" is currently reachable five ways, each with different behaviour:

| # | Entry point | Mechanism | Running-scan guard |
|---|---|---|---|
| 1 | `POST /competitors/{id}/scan-now` (`api/competitors.py:152`) | inline for `shopify_json`/`salla_json`, else `.delay()` | **none** |
| 2 | `POST /competitors/scan-all` (`api/competitors.py:52`) | mixed queue/inline, `asyncio.Semaphore(4)` in-request | unlocked read |
| 3 | `GET /cron/scan-due` (`api/cron.py:20`) | sequential inline | unlocked read |
| 4 | Celery beat (`workers/tasks.py:163`) | `.delay()` per due competitor | unlocked read |
| 5 | `frontend/src/lib/api.ts:14` | client-side worker pool calling #1 | **none** |

Pathway #5 is what the UI actually uses (commit `3622524`), which makes #2 dead code from
the UI's perspective while remaining a public route. Concurrency policy for bulk scanning
currently lives in the browser.

Consequences confirmed in the audit:

- `scan-now` returns two different response shapes and never checks for a running scan.
- The three guards are read-then-act races with no lock; duplicate concurrent scans of one
  competitor are possible.
- Because `events` has no `scrape_run_id`, overlapping scans send overlapping notification
  sets (ARCH A-5).
- On Vercel there is no Celery consumer, so `.delay()` scans are enqueued and never run —
  while the API returns a `task_id` implying success.

## Decision

**One pathway.** Every trigger — UI, bulk, scheduler, cron — converges on the same two use
cases:

```
any trigger → RequestCompetitorScan(competitor_id, trigger)
                 ├─ pg_advisory_xact_lock(competitor_id)
                 ├─ reject if a non-terminal ScrapeRun exists (return its id)
                 ├─ INSERT ScrapeRun(status=QUEUED, trigger=…)      [TX]
                 └─ enqueue ProcessCompetitorScan(scrape_run_id)

worker        → ProcessCompetitorScan(scrape_run_id)
                 ├─ QUEUED → RUNNING (guarded)                      [TX]
                 ├─ ScraperAdapter.scan(ctx)          (no DB session held)
                 ├─ domain.reconcile(...) → ChangeSet (pure)
                 └─ apply ChangeSet + events + outbox + SUCCEEDED   [ONE TX]
```

Specifically:

1. **The `ScrapeRun` is the durable job record**, created before any work begins. It gains
   a real state machine (`QUEUED → RUNNING → SUCCEEDED|FAILED|RETRYING|ABANDONED`) and a
   `trigger` column.
2. **The trigger is recorded, never branched on.** There is no behavioural difference
   between a manual scan and a scheduled one.
3. **Idempotency** comes from a PostgreSQL advisory lock plus a partial unique index
   preventing two non-terminal runs per competitor. A duplicate request returns the
   existing run id rather than creating a second run.
4. **`ScanAllCompetitors` calls `RequestCompetitorScan` in a loop** on the server. The
   client's role is to send one request and poll.
5. **The API layer stops importing worker internals.** Both routes and Celery tasks call
   use cases.

Full lifecycle, retry semantics, and transaction boundaries:
`docs/SCRAPING_ARCHITECTURE.md` §2.1.

## Alternatives considered

**Keep inline execution for fast JSON scrapers, queue only Playwright.** This is today's
rule and it is the direct cause of the two-response-shape problem and of the Vercel
silent-drop. Rejected as a *policy*; retained as a *deployment option* — see below.

**Keep the client-side fan-out and just add a server guard.** Rejected. It leaves
eligibility and concurrency policy in the browser, unauditable and unenforceable, and
still abandons work when the tab closes.

**Make everything synchronous and delete Celery.** Tempting given the Vercel topology, and
genuinely simpler. Rejected because a full Playwright scrape can exceed any reasonable HTTP
timeout, and because losing the durable queue removes the retry story entirely.

**Make everything asynchronous with no inline option.** Rejected as stated, because it
would break the Vercel deployment outright — there is no worker there. Resolved instead by
separating *policy* from *execution*: the pathway is always "create a durable run, then
enqueue", and whether the queue is drained by a separate worker process or by an in-process
runner is a deployment configuration. On Vercel the cron invocation drains the queue
inline, in chunks, within its budget; under Compose a real worker drains it continuously.
Same code, same state transitions, different runner.

## Consequences

**Positive**

- One implementation of scan policy. Rate limits, guards, and status live in one place.
- Duplicate concurrent scans become a database-level impossibility rather than a race.
- Every scan is durably recorded before work starts, so a lost worker or a closed browser
  tab leaves a recoverable `QUEUED` row instead of nothing.
- `scan-now` gets a single response shape (`202` + run id), which makes the frontend
  contract typable.

**Negative**

- **The UI's behaviour changes**: "Scan" stops returning results synchronously and starts
  returning a run id to poll. This is a user-visible change and must be implemented
  deliberately, not as a side effect of refactoring.
- Requires migrations: `scrape_runs.trigger`, the status enum, the partial unique index,
  and `events.scrape_run_id`.
- Needs a reaper for `RUNNING` rows that exceed their deadline — a component that does not
  exist today.
- The Vercel inline-drain runner is genuinely more complex than the current
  "just await it" code, because it must chunk work to fit 300 seconds and resume next run.

**Neutral**

- The unused `POST /competitors/scan-all` endpoint is replaced rather than deleted
  outright, preserving the route for any external caller.

## Dependencies

Depends on ADR 0002 (migrations must be trustworthy before adding constraints) and ADR
0006 (the deployment topology determines the runner). Enables ADR 0004 and the outbox work.
