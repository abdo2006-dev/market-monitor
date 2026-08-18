# Market Search Architecture and Trust Contract

Current as of Phase 1C. This document describes the implemented `/search` daily-use
workflow, not a future search-service design.

## 1. Request flow and ownership

```text
MarketSearch.tsx
  -> 250 ms debounced GET /api/search/suggestions?q=...
       -> SQL candidate superset (active products and competitors)
       -> existing Python fuzzy score
       -> market identity (collection, base, mutation)
       -> grouped suggestions
  -> user/keyboard selects one representative product
  -> GET /api/search/compare?product_id=...
       -> exact target and unchanged identity/comparison rules
       -> SQL exact-alias fast path, then full fallback for unresolved competitors
       -> one best representative per active competitor
       -> bounded Sync evidence queries and direct snapshot history
       -> trust classification, currency markets, and price-change context
  <- typed SearchCompareResponse
       -> React formats backend-owned market rules; it does not recalculate them
```

The authoritative matching code remains in
`backend/app/api/search_dashboard_settings.py`. Phase 1C deliberately did not perform the
larger module split proposed by the target architecture: preserving the regression-protected
matcher was more important than a cleanup refactor. Pure trust classification lives in
`app.domain.search_trust`; Pydantic response models own the HTTP contract.

The unused `/api/search/products` and external/manual batch routes remain unchanged.

## 2. Matching pipeline

Suggestions preserve the Phase 1A algorithm:

1. normalize case, punctuation, and whitespace;
2. generate full, four-character, and six-character token variants;
3. use `ILIKE` to load a broad candidate set;
4. score substring, token overlap, whole-string sequence similarity, and token similarity;
5. discard scores below `0.34`;
6. identify `(collection, base, mutation)` and group equivalent listings;
7. rank by match score, competitor coverage, and same-currency observed price.

Compare preserves the collection compatibility, mutation equality, alias generation, and
`0.86` representative threshold. It first asks PostgreSQL for products sharing a substantial
base-token prefix. A score-1 alias match is definitive; every competitor without one is
then searched across all of its active products with the original matcher. There is no
compare cap. This guarded fast path cannot silently truncate fuzzy comparison coverage,
and an explicit regression protects a fuzzy match outside the SQL shortcut.

Suggestion candidate retrieval remains capped at 1,000. The response now exposes
`candidates_considered` and `candidate_limit_reached`, uses product ID as a deterministic
timestamp tie-break, and the UI asks the operator to add another word when the cap is
reached. This is an honest limitation rather than a hidden complete-search claim.

## 3. Search trust semantics

Search never hides a stored observation merely because it is degraded. Each competitor
row has two independent concepts:

- `coverage_state`: what the latest durable catalog attempt proves about the competitor;
- `price_reliability`: whether this product price may participate in the reliable range.

Coverage states:

| State | Exact rule |
|---|---|
| `current_complete` | Latest relevant terminal run is `success + complete`, and its observation date satisfies the current Cairo morning cycle. |
| `partial` | Latest relevant terminal run succeeded with partial catalog evidence. |
| `suspicious_empty` | Latest relevant terminal run succeeded but the empty catalog was not trusted as complete. |
| `failed` | Latest relevant terminal run failed or was abandoned. |
| `stale` | Latest terminal evidence is complete, but its observation predates the required Cairo cycle. |
| `unknown` | No V2 terminal lineage exists, or the latest terminal evidence cannot establish completeness. |

The required Cairo cycle follows the actual scheduler: yesterday's complete catalog
remains the relevant cycle until the 08:47 `Africa/Cairo` recovery invocation. At and
after 08:47, today's complete catalog is required. `ZoneInfo` makes this DST-safe. This is
not an arbitrary minute-age threshold; the API still exposes all absolute timestamps and
ages.

A price is `reliable` only when all of these hold:

1. coverage is `current_complete`;
2. `Product.last_observed_run_id` is the latest complete run;
3. `Product.last_observed_at` is present;
4. the price and currency are present; and
5. stock is confirmed `in_stock`.

An out-of-stock, partial, failed, stale, or legacy price remains an observed price and is
shown with its warning. Active Sync is an overlay, not a coverage state: a prior complete
observation remains visible and may remain reliable while the new run is in progress.
HTTP 202 or an active run never changes the label to refreshed.

The API distinguishes product observation age from complete-catalog age. A product may
therefore say “Observed 10 min ago” and “Complete catalog yesterday” at the same time.
Producing and active run IDs are available under progressive disclosure; legacy rows stay
honestly null.

## 4. Market statistics

Statistics are grouped by currency. Numeric values from USD, EUR, or any other currency
are never combined, ordered against each other, or converted.

For each currency the backend returns:

- lowest reliable price and competitor;
- lowest observed price and competitor;
- highest reliable price;
- median reliable price;
- observed and reliable price counts.

The overall summary returns matched competitors, trustworthy/current observations,
degraded or unknown observations, active Sync count, and whether no reliable price exists.
Null prices are excluded. Out-of-stock prices participate in “lowest observed” but not the
reliable range. Difference-to-market uses the same-currency lowest reliable price only; if
none exists, no substitute reference is invented.

## 5. Price-change context

Search reads `product_snapshots` directly in one bounded-by-result-set query. It finds the
latest snapshot representing the current price and the preceding different price in the
same currency. The response contains direction, absolute amount, percentage, observation
time, and nullable run ID.

Events are not used as price-history authority. A single creation snapshot, a currency
change, or history that does not reconcile to the current price produces no change claim.

## 6. Performance measurements

Method: local PostgreSQL 16, 12 competitors × 1,250 active products = 15,000 products,
five warm ASGI requests per case, median reported. The dataset includes one logical
Batwing listing per competitor and a broad 14,988-row `Market Item` family. No live or
production data was used.

| Case | Before | After | Notes |
|---|---:|---:|---|
| Exact suggestions | 72.71 ms | 72.66 ms | one query; unchanged within noise |
| Typo suggestions (`batwng`) | 62.05 ms | 59.94 ms | existing fuzzy behavior preserved |
| Broad suggestions | 204.48 ms | 208.51 ms | 1,000 candidates; cap now explicit |
| No result | 69.98 ms | 71.52 ms | one query |
| Compare (`Batwing`) | **967.83 ms** | **91.40 ms** | 90.6% faster |
| Compare handler, unprofiled | 942.60 ms | 51.70 ms | common exact-alias path loaded 12 instead of 15,000 comparison rows |
| Search serialization | 0.45 ms | 0.84 ms | richer trust payload; still negligible |

Baseline compare used three SQL queries and spent 73.51 ms in PostgreSQL; the rest was
15,013 Python identity calculations and fuzzy comparisons. Phase 1C uses four queries for
a legacy/no-lineage response and five when producing-run evidence exists; its profiled DB
time was 57.07 ms. Query count increased by one or two bounded evidence queries while
total latency fell by about 875 ms.

`EXPLAIN (ANALYZE, BUFFERS)` on the 15,000-row local dataset showed the exact token-prefix
candidate scan completing in 7.60 ms and the intentionally broad suggestion scan/sort in
10.22 ms. PostgreSQL was not the bottleneck, so Phase 1C adds no extension, migration, or
index. `pg_trgm` becomes worth reconsidering only when measured SQL candidate retrieval,
not Python or network/serialization time, stops meeting the daily interaction target.

## 7. Frontend behavior

- 250 ms debounce, 12-result limit, stable query keys, and no catalog preload;
- arrow-key navigation, Enter selection, Escape dismissal, combobox/listbox semantics,
  visible focus, and reduced-motion support;
- loading skeletons, no-result guidance, retryable API errors, and an explicit
  no-reliable-price warning;
- separate reliable/observed/median/high summary metrics;
- concise competitor cards with technical evidence in `<details>`;
- desktop, tablet, and mobile layout; Search alone collapses the fixed sidebar below
  760 px, and a 390 px browser check has no horizontal overflow;
- “Refresh market data” calls one durable `/api/sync/all` request, polls its request ID,
  and invalidates Search only after a terminal outcome.

Vitest + Testing Library is the smallest frontend test harness added for this critical
page. It covers loading, success, partial degradation, no reliable price, no suggestions,
API failure, active Sync, and truthful HTTP-202 wording. It tests visible behavior rather
than CSS implementation.

## 8. Known limitations

- Suggestions still use a conservative 1,000-row candidate cap. It is now visible; add a
  more selective query before considering a search service.
- A comparison with no exact-alias match at some competitors falls back across those
  competitors' active products to preserve fuzzy behavior. Rare broad fallback cases can
  therefore be slower than the measured all-competitor exact-alias path.
- Market vocabulary remains hardcoded in the existing authoritative Search module and is
  duplicated with Export/scraper vocabulary. Phase 1C did not risk that broader taxonomy
  migration.
- Search trust is cycle-aware for the accepted Cairo scheduler. A future execution
  topology with a different promised cadence must change this policy and its tests.
- Snapshot history records changes, not every observation; Search can explain the latest
  price change but is not a dense analytics chart.
- The API is typed with hand-written Pydantic/TypeScript Search models. Application-wide
  OpenAPI generation remains deferred; Phase 1C does not migrate unrelated routes.
