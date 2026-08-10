# Scraping Architecture

Part 1 documents every scraping strategy that exists today, traced from entry point to
persisted result. Part 2 defines the V2 adapter contract and scan lifecycle.

---

# Part 1 — Current implementation

All of it lives in one file: `backend/app/services/scraper.py` (1,195 lines).

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

Requires a shop domain, an access token, and collection handles. If not configured, it
attempts to *discover* them (see §1.3). Queries `collection(handle:)` with cursor
pagination, 250 per page, capped at `max_products` (default 500).

**2. `/products.json` via aiohttp** (`:161` → `:205`)

If `include_all_products` and `prefer_all_products_first` (both default true), fetches
`{base_url}/products.json?limit=250&page=N` for N in 1..max_pages. Stops on empty page,
HTTP ≥ 400, or any exception. Uses a certifi SSL context (`:355`).

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

Default cap: `max(max_pages * 250, 250)` products. At the default `max_pages=5` that is
1,250 individual page fetches for one competitor.

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

# Part 2 — V2 target

## 2.1 Authoritative scan lifecycle

One lifecycle. The trigger is recorded, never branched on.

```
   HTTP request │ bulk request │ scheduler │ cron
                └──────┬───────┴─────┬─────┘
                       ▼
          RequestCompetitorScan(competitor_id, trigger)
             │
             ├─ pg_advisory_xact_lock(hash(competitor_id))
             ├─ reject if a non-terminal ScrapeRun exists → return existing id
             ├─ INSERT ScrapeRun(status=QUEUED, trigger=trigger)        [TX commits]
             └─ enqueue ProcessCompetitorScan(scrape_run_id)
                       │
                       ▼
          ProcessCompetitorScan(scrape_run_id)          (worker)
             │
             ├─ [TX] load run; QUEUED → RUNNING (guarded); commit
             │
             ├─ build ScrapeContext from the competitor
             ├─ adapter = registry.get(competitor.strategy)
             ├─ observations = await adapter.scan(ctx)      ← NO DB SESSION HELD
             │     may raise TransientScrapeError | PermanentScrapeError
             │
             ├─ [TX] ─────────────────────────────────────────────────┐
             │    existing = product_repo.list_for(competitor_id)     │
             │    changeset = domain.reconcile(existing,              │
             │                                 observations,          │
             │                                 policy, now)           │ ONE
             │    product_repo.apply(changeset)                       │ TRANSACTION
             │    snapshot_repo.add(changeset.snapshots, run_id)      │
             │    event_repo.add(changeset.events, run_id)            │
             │    outbox.enqueue_for(changeset.events)                │
             │    run.status = SUCCEEDED, counts                      │
             │  ──────────────────────────────────────────────────────┘
             │
             └─ on error:  TransientScrapeError → RETRYING, re-enqueue (bounded backoff)
                           PermanentScrapeError → FAILED + ScrapeFailed event → outbox
                       │
                       ▼
          DeliverNotifications                          (separate worker)
             └─ [TX] claim N outbox rows FOR UPDATE SKIP LOCKED
                     → Notifier.send(entry)   (idempotency key = outbox id)
                     → delivered | attempts+1, available_at = backoff | dead-letter
```

### State transitions

| From | To | Trigger |
|---|---|---|
| — | `QUEUED` | `RequestCompetitorScan` |
| `QUEUED` | `RUNNING` | worker picks it up |
| `RUNNING` | `SUCCEEDED` | reconciliation committed |
| `RUNNING` | `FAILED` | permanent error |
| `RUNNING` | `RETRYING` | transient error, attempts < max |
| `RETRYING` | `QUEUED` | backoff elapsed |
| `RUNNING` | `ABANDONED` | reaper: exceeded deadline |

`SUCCEEDED`, `FAILED`, `ABANDONED` are terminal.

### Idempotency

- **Request** is idempotent per competitor: the advisory lock plus a partial unique index
  on non-terminal runs means a second request returns the existing run id rather than
  creating a duplicate. This is what the current unlocked read cannot guarantee.
- **Processing** is idempotent by `scrape_run_id`: the `QUEUED → RUNNING` transition is a
  guarded update, so a redelivered Celery message finds the run already `RUNNING` and
  exits.
- **Delivery** is idempotent by outbox row id, which becomes the notification's
  idempotency key.

### Locking

`pg_advisory_xact_lock` keyed on the competitor id, held only for the short request
transaction — never across the scrape itself. Concurrency between different competitors is
unaffected.

### Retries

Only `TransientScrapeError` (timeouts, 429, 5xx, connection resets) is retried. Bounded:
3 attempts, exponential backoff with jitter. Every attempt is recorded on the run.
`PermanentScrapeError` (404, misconfiguration, parse failure) is never retried — retrying
it just repeats the same request against someone else's server.

### Failure semantics

- A failed scan **never** deactivates products. The current empty-result guard
  (`_should_reject_empty_scrape`) already achieves this and must be preserved as an
  explicit domain rule.
- Failure produces a `ScrapeFailed` event through the outbox, so failure alerts obey the
  same enable/disable switch and delivery guarantees as every other notification. Today
  they bypass both.

### Transaction boundaries

Three short transactions, none spanning network I/O:

1. Request: lock + insert `ScrapeRun`.
2. Claim: `QUEUED → RUNNING`.
3. Apply: products + snapshots + events + outbox + terminal status — **atomic**.

Notification delivery commits separately, per batch. This is the deliberate at-least-once
boundary: an event is durably recorded before any webhook is attempted.

## 2.2 Adapter contract

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
class ScrapeResult:
    observations: list[ProductObservation]
    pages_fetched: int
    strategy_used: str            # which fallback actually produced the result
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

**No test in any layer may perform a live network request.** The `http` port is injected
with a fixture-backed fake. CI must never touch a real storefront — for correctness, for
speed, and because scraping a third party from shared CI infrastructure is not acceptable
behaviour.
