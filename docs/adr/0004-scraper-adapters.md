# ADR 0004 — Scraper adapter architecture with per-platform contract tests

**Status:** Accepted (Phase 0, 2026-08-10)

## Context

`backend/app/services/scraper.py` is 1,195 lines implementing three platforms —
Shopify (with five fallback strategies), Salla, and generic Playwright — plus sitemap
crawling, JSON-LD parsing, Shopify Storefront credential discovery, and Roblox-specific
category inference.

There is no interface. `scrape_competitor` dispatches on a free-text `scrape_type` string
and returns `list[dict]` with an undeclared, unvalidated shape. Every consumer re-guesses
the shape with `.get()`.

The practical consequence is visible in the commit history: `9660f6b`, `8e5f78c`,
`4a6adea`, `f080dc0`, and `f346f70` are all fixes to the Shopify fallback chain. Each
touched shared code — the dispatcher, `normalize_url`, `parse_price`, `_regex_first` — that
Salla and Playwright also depend on. No test in the suite would have detected a
cross-platform regression, because the tests cover pure helpers and dict extraction, not
per-platform behaviour.

Additional problems found:

- `external_id` is constructed differently per strategy and is `None` for Playwright, so
  those competitors can only ever match by URL.
- In-scan deduplication is by `url` in some paths and `external_id or url` in others.
- `category` is `None` in some paths and the literal `"Uncategorized"` in others, so
  downstream code checks both.
- `price` is produced as `float` and stored as `Numeric(12,2)` — an implicit lossy
  conversion at the boundary.
- Business taxonomy (Roblox game names, competitor brand names) is hardcoded in the
  scraper at `:23` and `:942`.

## Decision

Introduce a `ScraperAdapter` protocol and one adapter per platform, each returning a
validated canonical `ProductObservation`.

```python
class ScraperAdapter(Protocol):
    strategy: ClassVar[ScrapeStrategy]
    def supports(self, competitor: CompetitorSnapshot) -> bool: ...
    async def scan(self, ctx: ScrapeContext) -> ScrapeResult: ...
```

Key points, with full definitions in `docs/SCRAPING_ARCHITECTURE.md` §2.2–2.3:

1. **`ScrapeResult`, not a bare list.** It carries `strategy_used` — which fallback
   actually produced the data. This is the single most useful diagnostic the current
   implementation discards.
2. **`ProductObservation` is validated on construction**: absolute URL, non-empty title,
   `Decimal` price, ISO-4217 currency, `StockStatus` enum, `category=None` when unknown.
   An adapter returning something invalid fails loudly instead of persisting a bad row.
3. **The HTTP client is injected** via `ScrapeContext`, so adapters are testable against
   recorded fixtures with no network and no monkeypatching.
4. **Shopify's five-strategy cascade stays inside the Shopify adapter.** It is genuinely
   Shopify-specific knowledge. What changes is that it can no longer affect Salla or
   Playwright.
5. **Shared pure helpers move to `infrastructure/scrapers/shared/`** with their own tests,
   so changing one is visible as changing a shared component.
6. **Roblox taxonomy leaves the scraper.** It is customer data that changes with the
   market, not code.

### Contract tests

Three layers, all fixture-based:

- **Shared contract suite** parameterized over every registered adapter, asserting the
  adapter-independent `ProductObservation` invariants.
- **Per-adapter fixture tests** under `tests/fixtures/scrapers/<adapter>/`, captured once
  from real responses, trimmed to the fields the parser reads, and scrubbed of tokens and
  cookies.
- **Fallback-ordering tests** for Shopify: given `products.json` returning 403, assert the
  httpx path runs and `strategy_used == "products_json_httpx"`.

**No test may make a live network request.** This is a correctness requirement (determinism),
a speed requirement, and an acceptable-behaviour requirement — CI must never scrape a
third-party storefront.

## Alternatives considered

**Split the file into modules without introducing an interface.** Cheaper, and it would
reduce the file size. Rejected: file size is the symptom. Without a contract there is still
no seam to test against and no guarantee that a Shopify change cannot alter Salla output.

**One adapter per *strategy* rather than per *platform*** (a `ShopifyProductsJsonAdapter`,
a `ShopifyGraphQLAdapter`, a `ShopifySitemapAdapter`). Rejected: the fallback ordering is
itself Shopify domain knowledge and needs to live somewhere. Hoisting it into a generic
orchestrator would make every platform's cascade a shared concern again — the exact
coupling being removed.

**A plugin system with runtime registration/entry points.** Rejected as overengineering.
Three adapters in one repository need a dict, not a plugin loader.

**Pydantic model instead of a frozen dataclass for `ProductObservation`.** Reasonable —
validation is the point, and Pydantic is already a dependency. Deferred to implementation;
either satisfies this ADR. Frozen dataclass plus explicit validation is preferred for the
domain layer's framework-independence, but this is not a decision worth blocking on.

## Consequences

**Positive**

- A Shopify change cannot silently break Salla or Playwright — the stated goal.
- Adding a platform means writing one adapter and one fixture set, with the shared contract
  suite applying automatically.
- `strategy_used` makes production diagnosis possible; today there is no way to tell
  whether a result came from `products.json`, GraphQL, or a sitemap crawl.
- Canonical observations remove the `.get()`-everywhere pattern downstream and fix the
  `float`/`Decimal` and `None`/`"Uncategorized"` inconsistencies.

**Negative**

- Fixtures must be captured from the **current** implementation before the refactor, or the
  refactor is unverifiable. This is a prerequisite task, not part of the refactor.
- Fixtures go stale as real storefronts change. They test *our parser*, not the live sites;
  a separate, manually-run smoke check against real sites is needed and must stay out of CI.
- Some duplication appears between adapters (pagination loops, dedup). Accepted —
  premature deduplication is what produced the current shared-mutable-helper problem.
- Migrating three platforms behind a new interface is meaningful work on the
  highest-risk code in the repository. It must proceed one platform at a time.

## Migration approach

1. Capture fixtures from current real output. **Prerequisite.**
2. Define `ProductObservation`, `ScrapeContext`, `ScrapeResult`, and the protocol.
3. Extract `ShopifyAdapter` first — highest usage, best fixture coverage — keeping
   `scrape_competitor`'s signature as a shim so nothing downstream changes.
4. Assert the adapter reproduces the captured fixtures byte-for-byte.
5. Repeat for Salla, then Playwright.
6. Remove the shim once all three are migrated and `ProcessCompetitorScan` consumes
   `ProductObservation` directly.
