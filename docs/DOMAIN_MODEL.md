# Domain Model

Current as of migration `0005_durable_sync_lifecycle`. PostgreSQL is the durable source of
truth; SQLAlchemy models live in `backend/app/models/__init__.py`.

## Entity relationships

```text
SyncRequest n───n ScrapeRun        through SyncRequestRun
Competitor  1───n ScrapeRun
Competitor  1───n Product
Competitor  1───n Event
ScrapeRun   1───n Event            nullable lineage for legacy history
ScrapeRun   1───n ProductSnapshot  nullable lineage for legacy history
Product     1───n ProductSnapshot
Product     1───n Event
```

## SyncRequest

A durable user/scheduler intent. One manual competitor request normally contains one run;
Sync All and the automatic morning request contain one run per active competitor. The
association is many-to-many because a second request may reuse an already non-terminal
competitor run without creating duplicate work.

| Field | Meaning |
|---|---|
| `id` | UUID returned to API clients and accepted by the worker CLI |
| `trigger` | manual, manual_all, automatic_morning, or compatibility origin |
| `idempotency_key` | optional unique request key; morning uses the Cairo local date |
| `requested_at` | durable intent time |
| `dispatch_status` | `not_requested`, `dispatched`, or `failed` |
| `dispatched_at` | provider acknowledgement time, not execution time |
| `dispatch_error_category` | safe server-side dispatch diagnosis |

Dispatch state never determines run state. A failed dispatch leaves queued work intact.

## ScrapeRun

One durable competitor job across bounded attempts. Unlike the legacy model, an attempt is
not a new row; `attempt_count`, claim token, retry time, and lease record its progress.

### Execution state

```text
queued ──claim──> running ──success──> success
                    │          └─────> stale_skipped
                    ├─retryable + budget──> retry_wait ──claim──> running
                    ├─permanent/exhausted─> failed
                    └─expired final lease─> abandoned
```

`success` means acquisition and reconciliation code completed. It does not by itself mean
catalog coverage was complete. `stale_skipped` is terminal success of execution with no
product mutation because a later complete observation already committed.

### Acquisition completeness

| Value | Meaning | May infer absence? |
|---|---|---:|
| `unknown` | queued/legacy evidence cannot establish coverage | no |
| `complete` | adapter reached a trustworthy catalog end | yes, if newer |
| `partial` | cap/truncation or known incomplete evidence | no |
| `suspicious_empty` | unexpected zero products | no |
| `failed` | no usable acquisition result | no reconciliation |

This separation answers independently “did the job execute?” and “is absence evidence
trustworthy?”. Exactly five full 250-item Shopify pages are partial because a sixth page
may exist.

### Time vocabulary

| Field | Meaning |
|---|---|
| `sync_requests.requested_at` | durable user/scheduler intent was recorded; not evidence that work started |
| `queued_at` | durable run creation |
| `started_at` | first successful worker claim, retained across retries; not market freshness |
| `claimed_at` | current attempt's claim |
| `heartbeat_at` / `lease_expires_at` | liveness/recovery evidence |
| `acquisition_started_at` | external work began |
| product/snapshot `observed_at` | server time captured when that product's response page/batch (or individual product page) was obtained; whole-acquisition completion is only the fallback |
| `acquisition_completed_at` | the adapter returned or raised; may be later than its last product evidence after local processing or a stall |
| `observation_started_at` / `observation_completed_at` | earliest/latest accepted product evidence in the result; an empty result falls back to acquisition completion |
| `reconciled_at` | the product decision transaction applied the result |
| `terminal_at` / `finished_at` | durable terminal outcome was committed |

Request, claim, acquisition completion, and reconciliation times do not order individual
prices. Each product uses `(observed_at, scrape_run_id)`; equal evidence timestamps use
the higher run ID deterministically. Whole-run coverage uses
`(observation_completed_at, scrape_run_id)`. Reconciliation/terminal time is operational
latency, not market freshness.

### Claim, retry, and evidence fields

- `claim_token` is a UUID fencing token; `claimed_by` is a bounded non-secret label.
- `attempt_count`, `max_attempts`, and `next_attempt_at` implement bounded retry/backoff.
- `products_found`, `pages_fetched`, `request_count`, `page_cap_reached`,
  `acquisition_strategy`, and `completeness_reason` make acquisition diagnosable.
- `failure_category` and `error_message` are safe summaries, never raw secret-bearing
  responses or stack traces.

The partial unique index `uq_scrape_runs_competitor_non_terminal` covers V2
`queued/running/retry_wait` rows. Legacy-trigger rows are excluded so rollback code can
remain available during the proof period. `ix_scrape_runs_claimable` supports the worker's
status/retry/queue ordering.

## Competitor

The storefront configuration and a small latest-attempt summary. `last_scan_at` and
`last_scan_status` remain compatibility fields. Durable freshness is derived from run
history so a failed/partial attempt cannot erase the last complete observation.

`selector_config` remains an untyped strategy-specific JSON object. Important current
keys include generic CSS selectors, Shopify discovery/GraphQL settings, Salla pagination,
`allow_empty_catalog`, and `max_products`. Typed adapter configuration remains future
work; invalid/unsupported configuration is a safe non-retryable Sync failure where it can
be identified.

## Product

The mutable current state for one competitor item. Phase 1B.1 identity remains:

- unique `(competitor_id, canonical_url)`;
- unique `(competitor_id, identity_key)` when the derived product key exists;
- title is never identity;
- Shopify variant-changing raw external IDs collapse to a product-level identity.

Phase 1B.2 adds:

- `last_observed_at`: newest accepted external observation time;
- `last_observed_run_id`: the run that supplied that current evidence.

An observation updates a product only when `(observed_at, scrape_run_id)` is newer than
the stored pair. A returning product becomes active and resets misses. An unobserved
product increments misses only during complete, newer catalog coverage and deactivates at
three consecutive misses. Partial, suspicious-empty, failed, stale, or older coverage does
not count as absence.

`last_seen_at` and `last_checked_at` remain compatibility timestamps. New freshness work
should prefer `last_observed_at` for the current market fact and run coverage metadata for
competitor-wide claims.

## ProductSnapshot

Append-only change history. It is written on product creation and when tracked fields
change, not for every unchanged observation. `observed_at` records when the external fact
was obtained; `checked_at` records persistence time. New V2 snapshots carry the actual
`scrape_run_id`. Historical rows stay null rather than receiving fabricated provenance.

The product-detail chart is therefore a change history, not a dense observation series.

## Event

Append-only business/audit changes: new product, price/stock change, removal, and scrape
failure. New V2 events carry `scrape_run_id`; terminal runner failure/abandon events also
name their run. Claim fencing and terminal checks prevent an old/replayed claim from
emitting duplicate events.

`old_value`/`new_value` remain unversioned JSON. `notification_sent` still mixes audit and
legacy Discord delivery concerns; an outbox is future work. Notification delivery does
not participate in the V2 Sync transaction or outcome.

## AcquisitionResult and ProductObservation

`app.domain.acquisition.AcquisitionResult` is an immutable boundary object containing:

- canonical observation dictionaries;
- strategy and acquisition/observation timestamps;
- product/page/request counts;
- completeness and page-cap evidence;
- safe completeness reason/warnings.

The existing multi-platform scraper remains the adapter implementation and now emits
telemetry into this contract. JSON/GraphQL/Salla pages receive one server-clock batch
timestamp; generic cards and sitemap product pages are stamped individually. The
acquisition boundary removes its reserved internal timestamp and ignores any public
`observed_at` value supplied by storefront data. Whole-acquisition completion is used
only when an adapter supplies no finer boundary.

The fully typed, framework-free `ProductObservation` and pure `reconcile()` target have not
yet been extracted; `services/detection.py` remains the persistence-aware reconciliation
implementation. Phase 1B.2 changed its contract in a backward-compatible way to accept
run lineage, observation time, and absence permission.

## Invariants currently enforced

1. Alembic is the only schema authority.
2. Product canonical URL and derived identity are unique per competitor.
3. At most one non-terminal V2 run exists per competitor.
4. One fencing token owns a running lease; only that token may reconcile.
5. No database transaction is held across storefront acquisition.
6. Only complete, newer catalog coverage may infer absence.
7. Older observations cannot overwrite newer current state.
8. V2 snapshots/events carry the run that produced them.
9. Failed acquisition cannot change product missing/removal state.
10. PostgreSQL, not Redis or GitHub, owns requests, attempts, and outcomes.

## Remaining domain work

- typed strategy configuration and scraper adapter ports;
- pure reconciliation/ChangeSet extraction;
- versioned event payloads and a notification outbox;
- one owner for market identity and collection taxonomy;
- Export provenance (Phase 1D).

## SearchTrustAssessment

`app.domain.search_trust` is the first Phase 1C Search policy extracted into the domain.
It consumes only immutable `RunEvidence`, competitor evidence, product observation facts,
stock state, and a clock. It has no FastAPI, SQLAlchemy, or React dependency.

It returns two separate decisions:

- competitor catalog coverage: `current_complete`, `partial`, `suspicious_empty`,
  `failed`, `stale`, or `unknown`;
- price reliability: `reliable`, `degraded`, `unknown`, or `unavailable`.

The daily lifecycle determines the required catalog date. Before 08:47 Cairo, yesterday's
complete catalog is still the relevant cycle; from 08:47 onward, today's complete catalog
is required. A reliable price must be directly linked to that latest complete run, have
an external observation timestamp and price/currency, and be confirmed in stock. The
policy always returns absolute ages and a warning; it never deletes degraded evidence.

Market statistics are application/API output rather than a stored aggregate. They are
calculated per currency from the trust decisions. Direct `ProductSnapshot` history, not
event payloads, is authoritative for recent price-change context. See
`docs/SEARCH_ARCHITECTURE.md`.
