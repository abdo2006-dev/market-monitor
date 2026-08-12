# Collection Export Architecture

Current as of Phase 1D. `/exports` is a synchronous, read-only daily workflow:
it prepares bytes in the browser, presents their provenance, and downloads only
after the operator chooses the prepared file.

## 1. Request flow

```text
Exports.tsx
  -> select explicit mode: live (default) | cached
  -> GET /api/exports/collection-prices (Axios blob request)
       -> validate selected competitor + absolute same-host public URL
       -> live: shared acquire_catalog() -> AcquisitionResult
       -> cached: active matching stored Product rows + durable run evidence
       -> preserve product file shape, attach file-level provenance headers
  <- Blob + typed header metadata
  -> show complete / partial / suspicious-empty / failed / stored state
  -> user confirms browser download
```

Exports never create `SyncRequest`, `ScrapeRun`, product, snapshot, or event
records. Live acquisition uses the same `AcquisitionResult` completeness rules
as Sync, but it is not a Sync run and never fabricates a run ID.

## 2. Modes and truth

| Requested mode | Actual source | Outcome |
|---|---|---|
| `live` (default) | `live` | Downloads only observations acquired for this request. Complete, partial, and suspicious-empty are distinct. |
| `live` | none | HTTP 502 `live_acquisition_failed`; no stored row is consulted. |
| `cached` | `cached` | Intentionally exports active stored rows that match the collection’s existing alias rules. |
| `cached` | none | HTTP 404 `cached_export_unavailable`; the UI offers a live attempt. |

There is no implicit or opt-in fallback mode in Phase 1D. The daily UI makes the
stored choice explicit, which is the smallest model that removes the prior
silent-substitution risk.

## 3. Completeness and freshness

`AcquisitionResult` supplies `complete`, `partial`, `suspicious_empty`, or
`failed`, pages fetched, cap evidence, safe reason, and observation boundaries.
Five full 250-product Shopify pages are `partial`, not complete. A partial file
is still downloadable because its observed rows are useful; its visible warning
states that unobserved products may exist. A suspicious empty live result is an
honest zero-row live file, never a stored success.

Cached files report `unknown` when no terminal V2 evidence exists. They include
the oldest/newest row observation, latest complete and terminal run IDs where
real, a count of rows not directly linked to the latest complete run, and the
shared Search coverage state. The collection reconstruction is necessarily
approximate: the current model stores product category/title, not durable
collection membership, so alias matching may include broad category matches.

The Cairo market-cycle boundary is not duplicated: `domain.market_cycle` owns
the 08:47 `Africa/Cairo` policy; `domain.search_trust.assess_catalog_coverage`
is reused for cached file-level coverage terminology. Search remains independent
of Export; Export does not calculate a product price-reliability range.

## 4. File compatibility and contract

The default CSV field order, JSONL row shape, JSON envelope (`competitor`,
`collection_url`, `products_count`, `items`), media types, title sort order, and
filenames are unchanged. Historical `scraped_at` remains file-generation time,
not an observation claim.

File-level metadata is carried in `X-Market-Monitor-Export-*` headers and typed
in `CollectionExportProvenance` in both Pydantic and TypeScript. It includes
requested mode, actual source, completeness, product/page counts, cap evidence,
safe reason, acquisition/observation timestamps, and cached coverage basis.

`include_provenance=true` is an explicit additive format extension: JSON adds a
`provenance` object, and CSV/JSONL append `observed_at`, `observed_run_id`, and
`coverage_state` to rows. Default files do not gain columns or keys.

Failures are structured as `{"detail": {"code", "message", "provenance"}}`.
The browser requests a blob, parses that safe failure response, and never
equates a successful HTTP transport with a complete catalog.

## 5. Security boundary

The route rejects malformed/non-HTTP URLs, credentials in URLs, foreign hosts,
localhost, and literal private/link-local/reserved IP targets. It retains the
same-host constraint against the selected competitor. DNS rebinding and
cross-host redirects remain adapter-level limitations: the existing scraper has
multiple provider clients, so Phase 1D does not falsely claim a single
redirect-validation boundary. No adapter exception, token, stack trace, or
database detail reaches the file client.

## 6. Performance and limits

Exports remain direct synchronous requests because the small daily collection
workload has no evidence requiring a queue, worker, cache, object storage, or
ExportRun table. A local Phase 1C-versus-Phase 1D helper profile used 1,250
observations, CSV + JSON serialization, 20 samples, and 20 repetitions per
sample: median **7.35 ms → 7.34 ms** and p95 **7.40 ms → 7.43 ms**. It measures
the deterministic row/serialization work only; provider/network acquisition is
deliberately excluded and remains the dominant live-request cost. A live request
still inherits the existing per-request adapter timeout and deployment request
ceiling. See `docs/PROJECT_STATUS.md` for release gates.
