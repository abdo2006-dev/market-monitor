# ADR 0007 — Product identity invariants and serialized reconciliation

**Status:** Accepted (Phase 1B.1, 2026-08-11)

## Context

Phase 1A reproduced a PostgreSQL race: two scans acquired the same catalogue, both loaded
an empty `products` table, and both inserted the same logical product. The in-memory URL
index was not an integrity boundary. One duplicate later accumulated misses and emitted a
false `product_removed` event.

Raw `(competitor_id, external_id)` is not a sound constraint. Shopify JSON emits
`product_id:variant_id` and chooses the first variant. Storefront GraphQL chooses the
first available variant (or first variant), and sitemap HTML similarly samples one offer.
All three emit one observation for the parent product, which is also the unit consumed by
Search, Export, and Sync. The sampled variant component may therefore change while the
logical product does not. Salla emits a product ID. Generic Playwright emits no external
ID. Raw URLs also carry fragments, tracking
parameters, `www.`, trailing slashes, and—on the current Shopify GraphQL path—a singular
`/product/` spelling instead of `/products/`.

## Decision

One logical product is scoped to one competitor and identified by either:

1. a conservative `canonical_url`; or
2. a nullable derived `identity_key` when the source supplies a stable product ID.

The raw `url` and `external_id` remain unchanged as source evidence. For Shopify
`product_id:variant_id`, `identity_key` uses only the product component. Salla's raw ID is
already product-level. Playwright uses only the canonical URL. Titles never participate
in identity.

PostgreSQL enforces:

- `UNIQUE (competitor_id, canonical_url)`; and
- a partial unique index on `(competitor_id, identity_key)` where the key is non-null.

Duplicate observations inside one payload are connected by either key and collapsed
deterministically before reconciliation. The richest observation wins with a stable
tie-break, and the conflict is logged with competitor and identity context.

Reconciliation takes `pg_advisory_xact_lock(namespace, competitor_id)` after network or
browser acquisition and before loading existing products. The lock covers the entire
read/decide/write transaction and is released on commit/rollback. Different competitors
do not block each other, and no database transaction is held across slow acquisition.

`ScrapeRun.started_at` is captured before acquisition and persisted with the initial
`running` record; the monotonic row ID is a deterministic tie-break if timestamps are
equal. After taking the advisory lock, reconciliation queries for a committed `success`
run for the same competitor with a later `(started_at, id)`. If one exists, the older
run does not apply product state or misses. Failed runs never participate in this query
and cannot erase a prior successful watermark. If a named product-identity constraint
still wins a race, the transaction is rolled back, structured context is logged, ORM rows
are reloaded, the competitor lock is reacquired, and the complete reconciliation decision
is retried once. A second collision fails the run; there is no loop.

Existing duplicates are never silently deleted by migration. Migration `0004` backfills
keys but refuses to continue if conflicts exist. The owner must run the read-only audit,
take a backup, save the merge plan outside the repository, and explicitly run the
consolidator. It retains the earliest `first_seen_at`, latest observation timestamps and
latest checked commercial state, repoints every snapshot and event, and only then removes
the redundant current-state rows. No snapshots or events are deduplicated or deleted.

## Alternatives considered

**Raw unique URL.** Rejected: tracking parameters, fragments, trailing slashes, `www.`, and
Shopify's two path spellings would permit known duplicates.

**Raw external ID.** Rejected: the Shopify value includes an observation-dependent
variant, while the model stores one row per product.

**Redis lock.** Rejected: PostgreSQL owns the durable invariant and the deployed worker
topology is unresolved. Correctness must not depend on the broker/cache.

**Lock only INSERT.** Rejected: the race is the complete read/decide/write operation,
including missing-product decisions.

**Hold a lock across acquisition.** Rejected: Playwright/network work can take minutes and
would unnecessarily hold a database connection and transaction.

**Durable scan-run uniqueness.** Deferred to Phase 1B.2. Phase 1B.1 records both overlapping
attempts but serializes their product effects.

## Consequences

Duplicate logical products from concurrent reconciliation are now prevented at both the
application and database layers. The duplicate → false removal chain is closed.

Canonicalization is deliberately conservative: unknown query parameters, locale prefixes,
and HTTP-versus-HTTPS remain distinct. Redirect targets are not discovered during
reconciliation. A future adapter may supply an explicit canonical URL with fixture-backed
evidence.

Observation ordering is protected at committed successful scan-start granularity. The
current status vocabulary has no `skipped_stale` state, so an older attempt that safely
applies no observation is still recorded as `success`; Phase 1B.2 must make that explicit.
The current untyped payload also has no per-product `observed_at`, so Phase 1B.2 must add
observation provenance for precise ordering within long, paginated acquisition.
