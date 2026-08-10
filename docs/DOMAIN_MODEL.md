# Domain Model

Part 1 describes the persisted model **as it exists today**. Part 2 describes the V2
target domain, which is a refinement of the same concepts, not a replacement.

---

# Part 1 — Current persisted model

Source: `backend/app/models/__init__.py`, `backend/alembic/versions/`.

## Entity relationships

```
Competitor 1───n Product         (ON DELETE CASCADE)
Competitor 1───n Event           (ON DELETE CASCADE)
Competitor 1───n ScrapeRun       (ON DELETE CASCADE)
Product    1───n ProductSnapshot (ON DELETE CASCADE)
Product    1───n Event           (ON DELETE SET NULL)
AppSettings  — singleton, id=1, unrelated
```

`ScrapeRun` has **no relationship to `Event` or `Product`**. This is the structural gap
behind risk A-5: nothing records which run produced which change.

## Competitor

The monitored storefront. Owns its own scan cadence, scraping strategy, and notification
target.

| Field | Type | Notes |
|---|---|---|
| `name` | String(255) | display only |
| `base_url` | String(500) | origin; also the SSRF allowlist for exports |
| `category` | String(100) | free text, e.g. "Roblox marketplace" |
| `active` | Boolean | inactive competitors are skipped by every scan path |
| `scan_frequency_minutes` | Integer | per-competitor cadence, default 60 |
| `scrape_type` | String(50) | **unconstrained**; handled values: `shopify_json`, `salla_json`, `generic_selector`, `custom` |
| `listing_urls` | JSON array | collection/category URLs; meaning varies by `scrape_type` |
| `selector_config` | JSON object | **untyped grab-bag** — see below |
| `discord_webhook_url` | String(500) | credential stored in plaintext |
| `last_scan_at` / `last_scan_status` | | denormalized from the newest `ScrapeRun` |

`selector_config` is the weakest point of the model. It is a schemaless JSON object whose
keys are read across three modules and mean different things per platform:

- Generic/Playwright: `product_card`, `title`, `price`, `url`, `image`, `stock`,
  `pagination_next`
- Shopify: `discover_collections`, `include_all_products`, `prefer_all_products_first`,
  `prefer_storefront_graphql`, `auto_discover_storefront_graphql`, `collection_handles`,
  `storefront_graphql{shop_domain, access_token, api_version, max_products}`,
  `max_sitemap_products`, `sitemap_concurrency`, `request_timeout_seconds`
- Salla: `platform`, `category_id`, `category_ids`, `category_name`, `per_page`, `source`,
  `currency`, `locale`
- Cross-cutting: `allow_empty_catalog`, `max_products`

Nothing validates these. A typo is silently ignored and the scraper falls back to a
default, so a misconfigured competitor looks like a scraping failure.

## Product

The current known state of one item at one competitor. Mutable — it is overwritten in
place on every scan.

Identity is **not** enforced by the database (no unique constraint). It is resolved in
`services/detection.py:235` as: match by `url`, else match by `external_id`, else treat as
new. Title matching was deliberately rejected (`detection.py:42`) because stores reuse
short item names — preserve this decision.

`external_id` format varies by source: `"{product_id}:{variant_id}"` for Shopify,
the raw id for Salla, `None` for generic Playwright scraping. It is a `String(255)` with
no format contract.

Lifecycle: `active=True` on every sighting with `consecutive_misses` reset to 0. A product
absent from a scan increments `consecutive_misses`; at 3 (`CONSECUTIVE_MISS_THRESHOLD`,
`detection.py:12`) it flips to `active=False` and emits `product_removed`. There is no
resurrection path in code — a returning product matches by URL and is set `active=True`
again, but no `product_returned` event is emitted.

## ProductSnapshot

Append-only history of a product's price/stock/title/image at a point in time. Written on
product creation and thereafter **only when something changed** (`detection.py:183`).

This means snapshots record *changes*, not *observations* — you cannot tell from the
snapshot table whether a product was checked and found unchanged, or not checked at all.
`ProductDetail`'s price chart is therefore a change history, not a time series. This is a
reasonable storage tradeoff but is undocumented and shapes what the data can answer.

`GET /api/products/{id}/history` returns every snapshot, unpaginated and unbounded.

## Event

An observed change worth reporting. The notification unit.

| `event_type` | Emitted at | `old_value` / `new_value` |
|---|---|---|
| `new_product` | `detection.py:81` | null / title, category, price, currency, stock, url |
| `price_increase` | `detection.py:124` | price, currency, category / price, currency, category, diff_amount, diff_percentage |
| `price_decrease` | same | same |
| `price_changed` | same, when one side is null | same |
| `stock_in` | `detection.py:146` | stock_status, category / stock_status, category |
| `stock_out` | same | same |
| `product_removed` | `detection.py:203` | url, title, category / null |
| `scrape_failed` | `tasks.py:142` | null / null, message only |

`old_value`/`new_value` are free-form JSON with **no schema and no versioning**. Consumers
index them positionally: `events.py:60` reads `new_value["category"]`,
`notification.py:210` reads `new_value["price"]`. A change to what detection writes
silently breaks both.

`notification_sent` is the delivery flag. It is set in memory before the transaction
commits (A-5) and is never set for `scrape_failed` events, which therefore accumulate
permanently unsent.

Semantically the events table is doing two jobs: it is the **audit log** (what changed)
and the **delivery queue** (what still needs sending). V2 separates these.

## ScrapeRun

One execution attempt against one competitor.

Status: `running` (set at creation, `tasks.py:48`) → `success` (`:81`) or `failed`
(`:135`). There is no queued state, no state machine, and **no reaper** — a process that
dies mid-scan leaves a permanently `running` row. Eligibility checks work around this with
a 30-minute cutoff (`api/competitors.py:172`) rather than reconciling the row.

Counters (`products_found`, `new_products_count`, `price_changes_count`) are written only
on success. Stock changes and removals are not counted at all.

No `trigger` column, so it is impossible to tell whether a run came from the UI, scan-all,
cron, or beat. No `scrape_run_id` foreign key on `Event` or `ProductSnapshot`, so a run's
effects cannot be reconstructed.

## AppSettings

Singleton row (`id=1`) exposed by `GET/PUT /api/settings` and rendered by the Settings
page. **Read by nothing else in the application.** Its eleven fields duplicate environment
settings that the scan pipeline reads instead. See `docs/CURRENT_SYSTEM.md` §3 and §5.

## Implicit domain concepts with no table

Concepts the code reasons about that have no persistent representation:

- **Market identity** — `_market_identity` (`search_dashboard_settings.py:692`) computes a
  `(collection, base, mutation)` triple to decide when two competitors' listings are the
  same item. Recomputed on every request, never stored, never correctable by a human.
- **Collection / game taxonomy** — `COLLECTION_ALIASES` and `COLLECTION_LABELS`
  (`:68-93`) plus a second copy of the same vocabulary in `services/scraper.py:942` and a
  third in `api/exports.py:170`. This is customer-editable business data living in three
  source files.
- **Mutation** — `MUTATION_PHRASES` (`:33-55`), a Roblox-specific item-variant concept
  (e.g. "Rainbow", "Blood Moon") used to prevent comparing a rare variant against a plain
  one. Genuine domain knowledge, hardcoded as a tuple.
- **Inferred sale** — a `stock_out` event reinterpreted as a probable sale
  (`:26`, `sales-trends`). The endpoint is careful to label this an inference
  (`:1027`), which is good; the concept itself has no model.

---

# Part 2 — V2 target domain

Same concepts, made explicit and testable. Not yet implemented.

## Core entities

**Competitor** — unchanged, plus a validated `ScrapeStrategy` value object replacing the
free-text `scrape_type` + untyped `selector_config` pair. Each strategy gets a typed
config model (`ShopifyConfig`, `SallaConfig`, `SelectorConfig`) validated on write, so a
misconfiguration fails at the API instead of at scan time.

**ProductObservation** — new, the canonical scraper output. A single immutable sighting of
one item, produced by an adapter and consumed by reconciliation. This is what
`scrape_competitor`'s untyped `list[dict]` becomes. Definition in
`docs/SCRAPING_ARCHITECTURE.md` §3.

**Product** — the reconciled current state, as today, plus a real uniqueness rule:
`UNIQUE (competitor_id, url)` and a partial `UNIQUE (competitor_id, external_id)`.

**ProductSnapshot** — unchanged in shape, gains `scrape_run_id` so history is attributable.

**ScrapeRun** — gains `trigger` (`MANUAL | BULK | SCHEDULED | CRON`) and a real state
machine:

```
QUEUED ──► RUNNING ──► SUCCEEDED
             │  │
             │  └────► FAILED      (permanent error)
             │  └────► RETRYING ──► QUEUED   (transient, bounded)
             └───────► ABANDONED   (reaper: RUNNING past deadline)
```

A partial unique index on `(competitor_id)` where status is non-terminal makes duplicate
concurrent runs a database error rather than a race.

**Event** — becomes purely an audit record. `notification_sent` moves out.
Gains `scrape_run_id`. `old_value`/`new_value` become typed payloads per event type with a
`payload_version` field so consumers can migrate.

**OutboxEntry** — new. `(id, event_id, channel, status, attempts, last_error,
available_at, delivered_at)`. Written in the same transaction as the events it refers to;
drained by `DeliverNotifications`. This is what fixes A-5.

**MarketIdentity** — promoted from a computed triple to a domain value object with an
optional persisted override table, so a human can correct a bad automatic grouping instead
of editing `MUTATION_PHRASES` in source.

**CollectionTaxonomy** — the alias/label vocabulary moves out of three source files into
one owner: either a configuration file or a database table. It is data, and it changes
when the market changes, not when the code changes.

## Core domain operation

```python
def reconcile(
    existing: Sequence[Product],
    observations: Sequence[ProductObservation],
    policy: DetectionPolicy,
    now: datetime,
) -> ChangeSet:
    ...
```

Pure. No session, no I/O. `ChangeSet` carries products to insert, products to update,
snapshots to write, and events to emit. This is the current `detect_changes` with
persistence lifted out — which is what makes the flood-control rule, the
`consecutive_misses` threshold, and the price-change thresholds testable for the first
time.

`DetectionPolicy` is where `MIN_PRICE_CHANGE_AMOUNT`, `MIN_PRICE_CHANGE_PERCENTAGE`, and
`IGNORE_KEYWORDS` finally become live — they are currently dead configuration
(`docs/CURRENT_SYSTEM.md` §5), and the Settings page that appears to control them
currently controls nothing.

## Invariants V2 must enforce

1. A product URL is unique per competitor. *(Database constraint — today: Python dict.)*
2. At most one non-terminal `ScrapeRun` per competitor. *(Partial unique index — today:
   an unlocked read.)*
3. Every `Event` names the `ScrapeRun` that produced it. *(FK — today: impossible.)*
4. A notification is delivered at most once per outbox entry. *(Idempotency key — today:
   no guarantee.)*
5. A product is never deactivated by a failed scan. *(Already holds, via
   `_should_reject_empty_scrape` — preserve it.)*
6. Persisted schema equals migration state. *(Alembic-only — today: violated, A-1.)*
