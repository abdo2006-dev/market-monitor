# Data Flow

Current as of Phase 1B.2. `→` is a call and `[TX]` is a committed PostgreSQL
transaction. Sync V2 is the default; legacy execution remains only behind
`SYNC_EXECUTION_MODE=legacy`.

## Flow 1 — Manual competitor Sync

```text
Competitors.tsx
  → POST /api/sync/competitors/{id} [optional Idempotency-Key]
    → request_competitor_scan
      → lock competitor request key
      → validate active competitor
      → reuse its non-terminal run, or insert queued ScrapeRun
      → create SyncRequest and association                         [TX]
    → optional server-only GitHub workflow dispatch
      → record dispatched or safe dispatch failure                 [TX]
  ← 202 + Location + durable request/run status
  → poll GET /api/sync/requests/{uuid}
```

The first commit happens before provider dispatch. A token/network failure cannot erase
the request and is never represented as Sync success.

The compatibility route `POST /api/competitors/{id}/scan-now` delegates to this flow in
V2. Its direct inline/Celery implementation runs only in legacy mode.

## Flow 2 — Sync All

```text
Competitors.tsx
  → POST /api/sync/all
    → request_all_competitor_scans
      → one SyncRequest
      → each active competitor uses request_competitor_scan
      → existing non-terminal runs are associated, not duplicated [TX]
    → optional provider dispatch
  ← 202 with one aggregate and all per-competitor run IDs
```

React no longer selects competitors, controls concurrency, or fans out HTTP requests.
`POST /api/competitors/scan-all` is a V2 compatibility alias for the same use case.

## Flow 3 — Automatic Cairo morning Sync

After `.github/workflows/sync-v2.yml` reaches the default branch, it runs at **07:17 and
08:47 Africa/Cairo** every day. Each schedule entry uses the IANA timezone directly, so
Cairo daylight-saving changes require no UTC-offset edit:

```text
GitHub schedule
  → python -m app.workers.sync_worker --morning
    → Cairo local date
    → request key automatic:<YYYY-MM-DD>
    → run keys automatic:<YYYY-MM-DD>:<competitor-id>               [TX]
    → recover expired claims, then drain every currently eligible run
```

Both invocations use the same request/run idempotency keys. The second invocation recovers
or completes today's work and cannot blindly create another daily batch. It also drains a
manual request whose optional dispatch failed.

The authenticated `/api/cron/scan-due` and `/daily` routes converge on the same automatic
request in V2. The old Celery beat scan scheduler explicitly returns
`legacy_scheduler_disabled`; it would otherwise enqueue a full batch every minute.

## Flow 4 — Claim and lease

```text
worker startup
  → recover_expired_leases
    running + expired + attempts remain → retry_wait now             [TX]
    running + expired + attempts spent  → abandoned + failure event [TX]
  → claim_next_run
    SELECT eligible queued/retry_wait
      FOR UPDATE SKIP LOCKED
    → running, attempt + 1, worker label, UUID claim token,
      claimed/heartbeat/lease timestamps                            [TX]
```

The claim transaction commits before any storefront request. During acquisition a short
independent heartbeat transaction renews only the matching run/token. If a runner dies,
the lease expires. If another worker recovers it, the replacement UUID fences the old
process from reconciliation.

## Flow 5 — Acquisition

```text
process_claimed_run
  → load a minimal competitor configuration                         [short TX]
  → acquire_catalog                                                  [no DB TX]
    → existing Shopify / Salla / generic scraper
    → collect strategy, request/page count, cap evidence
  ← AcquisitionResult(observations, actual timestamps, completeness, evidence)
```

Completeness is separate from whether code executed:

| Acquisition | Reconcile observations | Infer absence |
|---|---:|---:|
| `complete` | yes, when newer | yes, when coverage is newer |
| `partial` | yes, when newer | no |
| `suspicious_empty` | no observed rows | no |
| `failed` | no | no |

For Shopify, a fifth full 250-item page means another page may exist. The run records
1,250 products, `page_cap_reached=true`, and `completeness=partial`. Salla cursor evidence
and generic “next page” evidence receive the same conservative treatment.

Failures become safe categories such as timeout, rate limit, temporary network, or invalid
configuration. Raw exception bodies, tokens, response content, and connection strings are
not persisted.

## Flow 6 — Reconciliation and freshness

```text
open reconciliation transaction
  → verify running status + exact claim token; lock run row
  → record acquisition completion and the actual product evidence window
  → transaction advisory lock for competitor
  → find later complete observation by
      (observation_completed_at, run_id)
    ├─ later exists: terminal stale_skipped, no product writes
    └─ otherwise detect_changes(... per-item observed_at, run_id, allow_absence)
         → observed newer products: update/create current state
         → changed rows: snapshot + event, both linked to run
         → only complete/newer coverage: count misses/removals
  → update competitor watermark and terminal run together            [TX]
```

`requested_at`, `started_at`, per-product `observed_at`, `acquisition_completed_at`,
`reconciled_at`, and `terminal_at` intentionally describe different clocks. Current
product truth uses server time at the response page/batch boundary, not when a runner
started, finally returned, or committed. Equal observation times use run ID as a stable
tie. A failed acquisition never calls reconciliation and creates no market evidence.

## Flow 7 — Retry and failure

```text
retryable failure + attempts remain
  → retry_wait + next_attempt_at with bounded exponential backoff    [TX]

non-retryable failure OR attempts exhausted
  → failed + safe category/message + linked scrape_failed event      [TX]
```

Failure does not call product reconciliation, advance product observation times, or alter
missing/removal state. A retry uses the same durable run; a completed/obsolete claim token
cannot create duplicate failure/change events.

## Flow 8 — Status and Search freshness foundation

- `GET /api/sync/requests/{uuid}` aggregates queued/running/retrying/success/partial/failed.
- `GET /api/sync/runs/{id}` exposes safe timestamps, attempts, counts, completeness, and
  failure detail.
- `GET /api/sync/freshness` exposes last complete observation, latest partial observation,
  last failed attempt, coverage completeness, and any active run.
- `products.last_observed_at` and `last_observed_run_id` identify current observation
  provenance for Phase 1C.

Search does not yet render or rank by this data. Export remains the existing synchronous
flow and its provenance defect remains Phase 1D work.

## Flow 9 — Execution provider and notification boundaries

The GitHub dispatcher is an infrastructure adapter invoked only after the request commit.
The worker CLI contains no GitHub API code and can run unchanged on Railway or locally.

Discord notification delivery remains in legacy Celery/API helpers. V2 Sync commits its
business result without waiting for delivery. Event/outbox delivery correctness is not
claimed by Phase 1B.2.
