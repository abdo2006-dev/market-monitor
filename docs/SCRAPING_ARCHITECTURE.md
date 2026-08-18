# Scraping Architecture

Part 1 documents every scraping strategy that exists today, traced from entry point to
persisted result. Part 2 defines the V2 adapter contract and scan lifecycle.

---

# Part 1 — Current implementation

The legacy multi-platform implementation remains concentrated in
`backend/app/services/scraper.py`; durable lifecycle ownership and telemetry wrapping live
in `application/sync.py` and `domain/acquisition.py`. Historical line references below are
navigation hints, not an API contract.

## 1.1 Dispatch

`scrape_competitor(competitor: dict, max_pages, page_delay, headless, user_agent)`
(`:51`) — the sole entry point, called from three places:

- `workers/tasks.py:67` (every scan)
- `api/exports.py:56` (synchronous collection export)

Dispatch (`:60-65`):

| Condition | Strategy |
|---|---|
| `scrape_type == "salla_json"` | `scrape_salla_json` (`:284`) |
| `scrape_type == "shopify_json"` | `scrape_shopify_json` (`:138`) |
| `scrape_type == "generic_selector"` **and** no `listing_urls` | `scrape_shopify_json` |
| otherwise | inline Playwright (`:67-135`) |

The third row is a silent fallback: a competitor configured for generic scraping with no
listing URLs is scraped as Shopify without any indication.

Returns `list[dict]`. The shape is undeclared and unvalidated. Keys produced:
`title, price, currency, url, image_url, stock_status, sku, external_id, category`.

## 1.2 Shopify — five strategies in sequence

`scrape_shopify_json` (`:138`) tries these in order and returns the first non-empty result:

**1. Storefront GraphQL, if `prefer_storefront_graphql`** (`:156` → `:372`)

Requires a shop domain and public Storefront token. If not configured, it attempts to
*discover* them (see §1.3). With collection handles it queries `collection(handle:)`;
without handles it queries the root `products` connection. Both use cursor pagination,
250 per page, and the shared validated page ceiling.

**2. `/products.json` via aiohttp** (`:161` → `:205`)

If `include_all_products` and `prefer_all_products_first` (both default true), fetches
`{base_url}/products.json?limit=250&page=N` for N in 1..max_pages. The Phase 1F default is
100 pages (a hard ceiling, not a target); normal acquisition stops on a short/empty page.
Repeated pages, malformed payloads, cap exhaustion, and mid-catalog failures emit safe
partial/failure telemetry rather than claiming complete coverage. Uses a certifi SSL
context (`:355`).

**3. `/products.json` via httpx** (`:202` → `:233`)

Runs **only if strategy 2 returned nothing**. Same URLs, different client, browser-like
headers (`Accept-Language`, `Cache-Control: no-cache`, `Pragma`, and a per-target
`Referer`), `follow_redirects=True`, 20-second timeout. Added by commit `f080dc0` to get
past bot filtering that rejects aiohttp.

**4. Per-collection targets** (`:172-179`)

If `discover_collections` (default true), fetches `{base_url}/collections.json` (`:848`)
and builds one `/collections/{handle}/products.json` target per collection with a non-zero
`products_count`, using the collection title as the category. `_shopify_targets` (`:860`)
also converts any `listing_urls` containing `/collections/` into product.json targets, and
appends the all-products target last. Deduplicated by URL.

**5. Custom storefront fallback** (`:181` → `:363`)

If everything above returned nothing: Storefront GraphQL again (with discovery), then
sitemap crawling (§1.4).

## 1.3 Storefront credential discovery

`_storefront_graphql_config` (`:434`) → `_discover_storefront_assets_text` (`:551`) →
`_storefront_access_token_from_assets` (`:476`)

The process: fetch the competitor's homepage, extract `.js` asset URLs, download up to 40
of them (following `import`/`from` chains), concatenate, then regex out
(a) a `*.myshopify.com` domain, (b) a value assigned near
`X-Shopify-Storefront-Access-Token`, and (c) an API version near `graphql.json`. The token
is then used to query the competitor's Storefront GraphQL API.

The fallback token pattern is `["']([a-f0-9]{32,})["']` (`:489`) — any 32+ character hex
string in any of those bundles. This will frequently match something that is not an access
token.

This is credential harvesting from third-party sites. See `docs/SECURITY.md` §4 for the
legal and operational assessment; it is flagged there, not here.

## 1.4 Sitemap fallback

`_scrape_sitemap_product_pages` (`:671`) — fetch `{base_url}/sitemap.xml`, recurse into
nested sitemaps (max 10, same-host only), collect URLs whose path matches `/products?/`,
then fetch each product page with `asyncio.Semaphore(10)` and parse it.

`_extract_product_from_storefront_html` (`:749`) parses JSON-LD `Product` blocks
(`:798`, handling `@graph` and arrays), with regex fallbacks against the raw HTML for
price (`price:$R[\d+]={amount:"..."`), currency, image (`cdn.shopify.com` URLs),
`gid://shopify/Product/(\d+)`, and an `availableForSale:(!0|!1)` minified-boolean probe.

Default cap: `max(max_pages * 250, 250)` products. At the Phase 1F default ceiling of 100,
that is a 25,000-product safety bound; normal sitemap exhaustion ends earlier.

## 1.5 Salla

`scrape_salla_json` (`:284`). Salla is a storefront platform with a public category API.

1. Resolve category ids: from `selector_config.category_ids`/`category_id`, or parsed from
   listing URLs (`/c123` or `?source_value=123`, `:1050`), or discovered by fetching the
   listing HTML and regexing `<salla-products-list source-value="123">` (`:1063`).
2. Resolve locale (`:1076`): configured, else a two-letter first path segment, else `en`.
3. Build `{origin}/{locale}/api/v1/products?source=categories&source_value[]={id}&per_page={n}&filterable=1&currency={c}`
   (`:1087`). `per_page` is capped at 32.
4. Force currency with a `Cookie: s-curr={currency}` header (`:302`) — commit `edaf754`
   pinned this because Salla returns localized prices otherwise.
5. Follow `data.cursor.next` for up to `max_pages` pages, re-localizing each cursor URL
   (`:1101`).

`_extract_salla_product` (`:959`) handles bilingual titles: `_english_text` (`:997`) splits
on `" - "` and takes the last part containing Latin characters. `_english_category`
(`:1007`) maps eight known Arabic category names to English via a hardcoded dict.

## 1.6 Generic Playwright

`scrape_competitor` inline (`:67-135`). Launches Chromium, one context, one page reused
across all listing URLs. Per listing URL: `goto(wait_until="domcontentloaded")`, sleep
`page_delay`, `query_selector_all(selector_config["product_card"])`, extract each card via
`_extract_product` (`:1125`), then follow `pagination_next` up to `max_pages`.

`_extract_product` falls back to `card.inner_text()[:200]` as the title when no title
selector matches (`:1137`) — which produces a "product" whose title is the entire card's
text. It returns `None` only if no URL was found.

Stock is inferred from text keywords (`_detect_stock`, `:40`).

If `product_card` is not configured, the loop logs a warning and breaks immediately
(`:97`) — yielding zero products, which the caller converts into a scan failure.

## 1.7 Cross-cutting behaviour

**Retries** There is no retry anywhere in the scraper. `tenacity` is in
`requirements.txt` and is never imported. Every failure path either breaks the loop or
returns an empty list. The only retry-like behaviour is the strategy cascade in §1.2.

**Timeouts** Inconsistent: Playwright 30,000 ms (`:17`); aiohttp
`selector_config.request_timeout_seconds` default 30 (`:149`), overridden to 8 by exports
(`api/exports.py:104`); httpx a hardcoded 20 s (`:250`); Storefront GraphQL inherits the
aiohttp session timeout. Celery caps the whole task at `task_soft_time_limit=600` /
`task_time_limit=900` (`celery_app.py:22-23`); Vercel caps the whole request at 300 s.

**Concurrency** Sitemap fetching uses `Semaphore(10)` (`:678`, configurable). Everything
else is sequential within one competitor. Across competitors, concurrency comes from
Celery worker count (2 in Compose), from the frontend's client-side pool (4), or from
`asyncio.gather` in the unused `/scan-all` endpoint (4).

**Rate limiting** None. There is no per-host throttle, no backoff, and no
`robots.txt` handling. `page_delay` (default 2.0 s) applies only to the Playwright path.

**User agent** `_shopify_user_agent` (`:277`) **overrides** the configured user agent with
a hardcoded Chrome string whenever the configured one contains "marketmonitor" or "bot" —
so the polite identifying UA in `.env.example` is discarded for Shopify requests. Commit
`c272021` made this the default.

**Normalization** `parse_price` (`utils/price_parser.py`) handles symbols, ISO codes, and
US/European decimal conventions. `normalize_url` resolves relative URLs.
`normalize_title` lowercases, strips punctuation, and collapses whitespace.

**Product identity** Each strategy constructs `external_id` differently:
`"{product_id}:{variant_id}"` for Shopify JSON and Storefront GraphQL, the raw id for
Salla, `None` for Playwright. Playwright-scraped competitors therefore can only ever be
matched by URL.

**Deduplication** Within a scan, by `url` (Shopify JSON, Playwright) or by
`external_id or url` (Salla, GraphQL, sitemap). Inconsistent, and none of it survives
into the database as a constraint.

## 1.8 Domain vocabulary embedded in the scraper

`_shopify_product_category` (`:926`) assigns a category by, in order: the caller's
collection name, Shopify's `product_type`, then **pattern-matching Roblox game names**
against the title, body HTML, and tags (`:942-951`: "murder mystery 2", "adopt me",
"grow a garden", "brainrot", "blox fruit"), then the vendor — unless the vendor is in
`GENERIC_SHOPIFY_VENDORS` (`:23`), a hardcoded list of competitor brand names.

This is business taxonomy in an infrastructure module, duplicated in
`api/search_dashboard_settings.py:56-93` and `api/exports.py:170`.

---

# Part 2 — V2 boundary and remaining target

## 2.1 Authoritative Sync lifecycle (implemented)

One V2 lifecycle. Trigger is recorded, never used to select reconciliation behavior.

```
   HTTP request │ bulk request │ scheduler │ cron
                └──────┬───────┴─────┬─────┘
                       ▼
          RequestCompetitorScan / RequestAllCompetitorScans
             │
             ├─ advisory request lock + DB partial uniqueness
             ├─ reuse non-terminal run or INSERT queued run
             ├─ associate run(s) with durable SyncRequest              [TX commits]
             └─ optional provider dispatch after commit
                       │
                       ▼
          sync_worker claim + ProcessCompetitorScan
             │
             ├─ [TX] FOR UPDATE SKIP LOCKED; running + UUID lease; commit
             ├─ heartbeat matching token while acquire_catalog runs
             ├─ AcquisitionResult                         ← NO DB TX HELD
             │    observations + strategy + timestamps + page/request evidence
             │    completeness: complete | partial | suspicious_empty
             └─ [TX] verify token + advisory reconciliation lock
                  ├─ stale newer coverage exists → stale_skipped
                  └─ apply allowed observations/misses + lineage + terminal run
```

### State transitions

| From | To | Trigger |
|---|---|---|
| — | `queued` | request use case |
| `queued` / `retry_wait` | `running` | atomic worker claim |
| `running` | `success` | reconciliation committed |
| `running` | `stale_skipped` | later complete observation already committed |
| `running` | `retry_wait` | retryable error and attempts remain |
| `running` | `failed` | permanent error or exhausted processing attempt |
| expired `running` | `retry_wait` | future worker recovery with attempts left |
| expired `running` | `abandoned` | future worker recovery after final attempt |

`success`, `stale_skipped`, `failed`, and `abandoned` are terminal.

### Idempotency

- **Request** is idempotent per competitor: the advisory lock plus a partial unique index
  on non-terminal runs means a second request returns the existing run id rather than
  creating a duplicate. This is what the current unlocked read cannot guarantee.
- **Processing** is fenced by `(scrape_run_id, claim_token)`. A replayed/expired owner
  cannot reconcile or emit history after the token changes.
- **Morning scheduling** is idempotent by Cairo local-date request and per-competitor keys.

### Locking

One transaction advisory lock serializes request creation per competitor. A separate
Phase 1B.1 advisory namespace serializes the complete reconciliation transaction. Neither
lock spans external acquisition. Database uniqueness is the request backstop; the UUID
claim token is the expired-owner fence.

### Retries

Timeouts, rate limits, transient 5xx/network errors, and lost runners are retryable.
Invalid/unsupported configuration is non-retryable where identified. Default budget is
three attempts with 60/120-second exponential delays; `attempt_count` and
`next_attempt_at` are durable. A GitHub invocation may leave future retry work for the
next manual/scheduled drain; Railway can poll continuously.

### Failure semantics

- `complete`: observed newer products update; unobserved products may count as missing.
- `partial`: observed newer products update; unobserved products do not change.
- `suspicious_empty`: no mass missing/removal inference.
- `failed`: no product reconciliation at all.
- Failure records a linked `scrape_failed` event with a safe category/message. Notification
  delivery remains legacy and is intentionally independent of Sync success.

### Transaction boundaries

Short transactions, none spanning network I/O:

1. Request/group association; optional provider dispatch happens after commit.
2. Recovery/claim and lease.
3. Short lease heartbeats while acquisition runs outside a transaction.
4. Products + snapshots + events + terminal status — **atomic**.

### Completeness evidence

The current adapters populate telemetry without exposing secret URLs or tokens. Shopify's
final full 250-item page at the configured ceiling is conservatively partial
(`page_cap_reached=true`). Salla uses a
remaining cursor at its cap; generic pagination uses an available next-page control at its
cap. An unexpected zero is `suspicious_empty` unless `allow_empty_catalog=true`. A
telemetry-proven HTTP/network failure raises `AcquisitionFailure` instead of masquerading
as an empty result.

`observed_at` is server-generated when the response page/batch is obtained (or per product
page/card where practical). The adapter ignores storefront-provided observation clocks;
whole-acquisition completion is only a fallback. Product update ordering is
`(observed_at, scrape_run_id)`, not request/start/acquisition-completion/commit time.

## 2.2 Adapter contract (partially implemented)

Phase 1B.2 introduced the concrete immutable `AcquisitionResult` boundary around the
existing scraper. Phase 1F adds fixture-backed contract coverage for the legacy boundary,
including long Shopify pagination, duplicate/malformed pages, root-products GraphQL,
Salla cursors, sitemap, generic observations, and sanitized failure evidence. The fully
split adapters/injected HTTP client below remain the target; do not describe them as
current code.

```python
class ScraperAdapter(Protocol):
    strategy: ClassVar[ScrapeStrategy]

    def supports(self, competitor: CompetitorSnapshot) -> bool: ...

    async def scan(self, ctx: ScrapeContext) -> ScrapeResult: ...
```

```python
@dataclass(frozen=True)
class ScrapeContext:
    competitor_id: int
    base_url: str
    listing_urls: tuple[str, ...]
    config: ShopifyConfig | SallaConfig | SelectorConfig   # validated, per strategy
    limits: ScrapeLimits          # max_pages, page_delay, timeouts, concurrency
    user_agent: str
    http: HttpClient              # injected — makes adapters testable without network
    clock: Clock

@dataclass(frozen=True)
class AcquisitionResult:
    observations: list[ProductObservation]
    pages_fetched: int
    request_count: int
    strategy: str                 # which fallback actually produced the result
    started_at: datetime
    completed_at: datetime
    completeness: Completeness
    page_cap_reached: bool
    completeness_reason: str | None
    warnings: list[str]
```

`ScrapeResult` rather than a bare list, because `strategy_used` is the single most useful
diagnostic the current implementation throws away — today there is no way to tell whether
a Shopify result came from `/products.json`, GraphQL, or a sitemap crawl.

`http` is injected so adapters can be tested against recorded fixtures with no network and
no monkeypatching.

## 2.3 Canonical `ProductObservation`

```python
@dataclass(frozen=True)
class ProductObservation:
    # identity
    url: str                       # absolute, normalized. REQUIRED.
    external_id: str | None        # stable per platform; "{product}:{variant}" for Shopify

    # content
    title: str                     # raw, as displayed. REQUIRED, non-empty.
    category: str | None           # None means unknown — never the string "Uncategorized"
    image_url: str | None
    sku: str | None

    # commercial
    price: Decimal | None          # Decimal, not float
    currency: str                  # ISO 4217, defaulted by the adapter
    stock: StockStatus             # IN_STOCK | OUT_OF_STOCK | UNKNOWN

    # provenance
    source_strategy: str
    observed_at: datetime
```

Three deliberate changes from today's dict:

1. **`price` is `Decimal`.** The scraper currently produces `float` and the column is
   `Numeric(12,2)`; the conversion is implicit and lossy at the boundary.
2. **`category` is `None` when unknown.** Adapters currently substitute the literal
   `"Uncategorized"` in some paths and `None` in others, so downstream code checks for
   both.
3. **`stock` is an enum.** Currently a free-text string compared with `==` in three places.

Validation on construction: URL absolute and non-empty, title non-empty, price
non-negative, currency a 3-letter code. An adapter returning an invalid observation fails
loudly rather than silently persisting a bad row.

## 2.4 Planned adapters

| Adapter | Replaces | Fallbacks it owns internally |
|---|---|---|
| `ShopifyAdapter` | `scrape_shopify_json` | products.json (aiohttp → httpx), collections, Storefront GraphQL, sitemap |
| `SallaAdapter` | `scrape_salla_json` | category-id discovery, cursor pagination |
| `PlaywrightAdapter` | inline generic scraping | pagination only |

The fallback cascade stays *inside* the Shopify adapter — it is genuinely Shopify-specific
knowledge. What changes is that it can no longer affect Salla or Playwright, because they
no longer share a dispatcher or a mutable helper surface.

Shared pure helpers (`parse_price`, `normalize_url`, `normalize_title`, JSON-LD parsing)
move to `infrastructure/scrapers/shared/` with their own tests, so a change to one is
visible as a change to a shared component rather than an invisible cross-platform edit.

## 2.5 Contract tests — the point of the refactor

The requirement: **a Shopify fix cannot silently break Salla or generic scraping.**

Three layers:

1. **Shared contract suite**, parameterized over every registered adapter. Each adapter
   supplies a fixture set; the suite asserts adapter-independent invariants — every
   observation has an absolute URL and a non-empty title, prices are `Decimal` or `None`,
   currency is ISO 4217, stock is a valid enum member, no duplicate `(url)` within one
   result, `strategy_used` is populated.

2. **Per-adapter fixture tests**, one directory per adapter:

```
tests/fixtures/scrapers/
  shopify/   products_json.json · collections_json.json · storefront_graphql.json
             sitemap.xml · product_page.html · empty_products.json · rate_limited_429.json
  salla/     category_products_page1.json · category_products_page2.json
             listing_page.html · arabic_titles.json
  playwright/ listing_page.html · listing_page_2.html
```

Fixtures are captured once from the current implementation's real responses, trimmed to
the fields the parser reads, and **scrubbed of tokens, cookies, and personal data**. They
are then frozen — a fixture change requires an explicit justification in review.

3. **Fallback-order tests** for Shopify specifically: given products.json returning 403,
   assert the httpx path is attempted and `strategy_used == "products_json_httpx"`. These
   are the tests that would have caught the regressions behind commits `4a6adea`,
   `f080dc0`, and `f346f70`.

**No deterministic test or required CI gate may perform a live network request.** The
`http` port is injected with a fixture-backed fake. Push/PR CI must never touch a real
storefront — for correctness, for speed, and because scraping a third party from shared
CI infrastructure is not acceptable behaviour.

The separate manual `app.diagnostics.live_coverage` runner is an observational operations
tool, not a test. It is sequential, database-free, sanitized, and isolated from push/PR
CI. Its canonical registry and status semantics are documented in
`docs/COMPETITOR_COVERAGE.md`.
