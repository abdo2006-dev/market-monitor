# Data Flow

Traced from source at baseline `f346f70`. Each flow shows the real call chain as it
exists today, followed by the intended V2 chain.

Notation: `→` is a call, `⇢` is an async queue hop, `[TX]` marks a database commit.

---

## Flow 1 — Manual single competitor scan

**Today**

```
Competitors.tsx:41 handleScanNow
  → api.ts:13 scanNow(id)
    → POST /api/competitors/{id}/scan-now
      → api/competitors.py:152 scan_now
        ├─ load Competitor, 404/400 guards
        ├─ IF scrape_type in {shopify_json, salla_json} OR RUN_SCANS_INLINE:
        │    → workers/tasks.py:25 _scrape_competitor_async(id)      ← API imports worker private fn
        │       (full scan runs inside the HTTP request; see Flow 4)
        │    ← returns {"message": "Scan completed", "result": {...}}
        └─ ELSE:
             ⇢ workers/tasks.py:20 scrape_competitor_task.delay(id)
             ← returns {"message": "Scan queued", "task_id": ...}
```

Two different response shapes from one endpoint, and the caller cannot know in advance
which it will get. `api.ts` does not distinguish them; `Competitors.tsx` ignores the
result entirely and just clears a spinner.

No check for an already-running scan on this path.

**V2 target**

```
UI → POST /api/competitors/{id}/scans
  → api layer: validate, authenticate
    → application/RequestCompetitorScan(competitor_id, trigger=MANUAL)
      → acquire advisory lock on competitor
      → create ScrapeRun(status=QUEUED)                                  [TX]
      ⇢ enqueue ProcessCompetitorScan(scrape_run_id)
  ← 202 Accepted {scrape_run_id, status: "queued"}
```

Always the same shape, always durable, always guarded.

---

## Flow 2 — Scan all

**Today** — the UI does *not* call the backend scan-all endpoint.

```
Competitors.tsx:36 scanAllMut
  → api.ts:14 scanAllCompetitors(competitors, concurrency=4)
      ├─ filters competitors by .active            ← client decides eligibility
      ├─ spawns 4 worker() coroutines              ← client decides concurrency
      └─ each worker loops: → scanNow(competitor.id)  → Flow 1, per competitor
      ← aggregates {total, completed, queued, failed, items[]}  ← client aggregates results
```

Orchestration — eligibility, fan-out, concurrency, error capture, aggregation — is
entirely client-side. Closing the browser tab mid-run abandons the remaining competitors.

**Also present but unreachable from the UI**: `POST /api/competitors/scan-all`
(`api/competitors.py:52`), which does its own split of queued vs inline and its own
`asyncio.Semaphore(4)` fan-out inside a single HTTP request.

So scan-all has *two* server-visible implementations and the live one is in the browser.

**V2 target**

```
UI → POST /api/scans/bulk
  → api layer
    → application/ScanAllCompetitors(trigger=MANUAL)
      → repository: list active competitors
      → for each: RequestCompetitorScan  (same use case as Flow 1)
                  → lock, ScrapeRun(QUEUED)                             [TX]
                  ⇢ enqueue
  ← 202 {requested: n, skipped: [...], scrape_run_ids: [...]}
```

The browser learns what happened by polling scrape-run state, not by driving the loop.

---

## Flow 3 — Scheduled scan

**Today** — two mutually unaware schedulers exist.

```
(A) Docker Compose only — Celery beat, every 60s
celery_app.py:27 beat "check-scan-schedule"
  → tasks.py:163 check_and_schedule_scans
    → tasks.py:169 _check_and_schedule_async
      → SELECT active competitors
      → per competitor: if last_scan_at < now - scan_frequency_minutes
                        AND no ScrapeRun(status='running', started_at > now-30m)
        ⇢ scrape_competitor_task.delay(id)                → Flow 4

(B) Vercel only — HTTP cron, daily at 08:00 UTC
vercel.json crons → GET /api/cron/daily
  → api/cron.py:62 daily
    → _check_auth(authorization)        ← no-op if CRON_SECRET unset
    → api/cron.py:20 scan_due (called as a plain Python function, not over HTTP)
      → per due competitor, SEQUENTIALLY:
        → tasks.py:25 _scrape_competitor_async(id)        → Flow 4, inline
    → tasks.py:210 _send_daily_summary_async               → Flow 6
  ← {"status":"ok", "scan": {...}, "summary": ...}
```

(A) and (B) never run in the same deployment. (A) has minute granularity and honours
per-competitor frequency; (B) runs once a day and scans everything due, in series, inside
one 300-second serverless invocation.

**V2 target** — one scheduler, one entry point:

```
scheduler (beat OR HTTP cron)
  → application/ScanAllCompetitors(trigger=SCHEDULED, due_only=True)
      → same RequestCompetitorScan path as Flows 1 and 2
```

The trigger becomes a recorded attribute of the `ScrapeRun`, not a different code path.

---

## Flow 4 — Scan execution, product detection and update

**Today** — `workers/tasks.py:25 _scrape_competitor_async`

```
open AsyncSessionLocal
  → SELECT Competitor; bail if missing/inactive
  → INSERT ScrapeRun(status='running')                                  [TX 1]
  → SELECT 1 FROM products WHERE competitor_id=? LIMIT 1   → is_initial_scan
  → build competitor_dict (id, base_url, listing_urls, selector_config, scrape_type)
  → services/scraper.py:51 scrape_competitor(dict, max_pages, page_delay,
                                             headless, user_agent)
      → dispatch by scrape_type → see docs/SCRAPING_ARCHITECTURE.md
      ← list[dict] of raw product observations
  → _should_reject_empty_scrape → raise if empty and not allow_empty_catalog
  → services/detection.py:15 detect_changes(session, competitor, products)
      → SELECT all Products for competitor
      → index by url, by external_id
      → per scraped item:
          match by url → else by external_id → else NEW
          NEW      : INSERT Product, flush, INSERT ProductSnapshot, INSERT Event(new_product)
          EXISTING : price differs?  → INSERT Event(price_increase|price_decrease|price_changed)
                     stock differs?  → INSERT Event(stock_in|stock_out)
                     title/image/url/category differ? → update in place
                     any change      → INSERT ProductSnapshot
                     always          : last_seen_at, last_checked_at, active=True, misses=0
      → unseen products: consecutive_misses += 1
                         at 3 → active=False, INSERT Event(product_removed)
      ← {"new_products": n, "price_changes": m}
  → update ScrapeRun(status='success', counts), Competitor(last_scan_*)  [TX 2]
  → Flow 5
```

Everything from `[TX 1]` to `[TX 2]` shares one session held open across the entire
network scrape.

**V2 target**

```
worker → application/ProcessCompetitorScan(scrape_run_id)
  → repo.mark_run(RUNNING)                                              [TX]
  → ScraperAdapter.scan(ScrapeContext) → list[ProductObservation]       (no DB session held)
  → domain: reconcile(existing_products, observations) → ChangeSet
  → repo.apply(ChangeSet) + repo.mark_run(SUCCEEDED) + outbox.enqueue(events)  [single TX]
```

Scraping happens outside the transaction; persistence and event emission happen inside
one.

---

## Flow 5 — Event generation and notification

**Today** — still inside `_scrape_competitor_async`, after `[TX 2]`

```
→ SELECT Event WHERE competitor_id=? AND notification_sent=false
        ← NOT scoped to this scrape run: events has no scrape_run_id column
→ SELECT Product WHERE id IN (event product ids)  → products_map
→ IF is_initial_scan OR pending new_product count > 25:
     mark every new_product event sent WITHOUT sending      (flood guard, tasks.py:110)
     notify_events = everything except new_product
   ELSE notify_events = all pending
→ services/notification.py:188 dispatch_event_notifications(...)
     per event:
       resolve webhook: competitor.discord_webhook_url or DISCORD_DEFAULT_WEBHOOK_URL
       skip if no webhook
       → notify_new_product / notify_price_change / notify_stock_change / notify_product_removed
           → send_discord_webhook (aiohttp POST, 10s timeout)
           → asyncio.sleep(1.0)                     ← serial, 1s per message
       on success: event.notification_sent = True   ← in memory only
       on exception: log and continue, flag stays false
→ COMMIT                                                                [TX 3]
```

Failure window: the webhook is delivered at the `send` call but only durably recorded at
`[TX 3]`. A crash in between re-sends on the next scan.

**V2 target**

```
ProcessCompetitorScan writes events + outbox rows in the SAME transaction as the products.

separately:
DeliverNotifications worker
  → claim outbox batch (SELECT ... FOR UPDATE SKIP LOCKED)
  → NotificationAdapter.send(event)                     (idempotency key = outbox id)
  → mark delivered / increment attempts / dead-letter after N               [TX per batch]
```

---

## Flow 6 — Daily summary

**Today**

```
(A) celery beat "send-daily-summary", every 86400s from beat start
(B) GET /api/cron/daily → api/cron.py:66

both → tasks.py:210 _send_daily_summary_async
  → COUNT Event(new_product) today
  → COUNT Event(price_increase|price_decrease) today
  → COUNT ScrapeRun(failed) today
  → COUNT DISTINCT ScrapeRun.competitor_id today
  → collect webhook set: DISCORD_DEFAULT_WEBHOOK_URL + every active competitor's webhook
  → if DISCORD_NOTIFICATIONS_ENABLED: send_daily_summary(webhook, summary) per webhook
```

`summary["biggest_drops"]` is hardcoded to `[]` at `:248`, so the "Biggest Price Drops"
field in the Discord embed always renders "None". `DAILY_SUMMARY_ENABLED` and
`DAILY_SUMMARY_TIME` are not consulted.

"Today" is computed as UTC midnight (`:220`) regardless of `DEFAULT_TIMEZONE`.

---

## Flow 7 — Failure handling

**Today** — the `except` block at `tasks.py:133`

```
any exception in scrape or detection
  → log
  → ScrapeRun.status = 'failed', finished_at, error_message = str(e)[:1000]
  → Competitor.last_scan_status = 'failed'
  → INSERT Event(scrape_failed, event_message=str(e)[:500])
  → COMMIT
  → webhook = competitor.discord_webhook_url or DISCORD_DEFAULT_WEBHOOK_URL
  → if webhook: notify_scrape_failure(webhook, name, error)   ← direct send, bypasses
                                                                 the event pipeline;
                                                                 notification_sent stays false
  → return {"status": "failed", "error": str(e)}               ← swallowed: Celery sees success
```

Consequences:

- `max_retries=3` / `default_retry_delay=60` on the task decorator are dead configuration.
  `self.retry()` is never called.
- A partially-applied detection pass is rolled back by the session's error handling, but
  the `ScrapeRun` row and the failure `Event` are committed — so a failed scan is
  recorded correctly even though nothing retries it.
- `scrape_failed` events accumulate with `notification_sent = false` permanently, since
  `dispatch_event_notifications` has no branch for that event type.
- `DISCORD_NOTIFICATIONS_ENABLED` is **not** checked on this path — failure webhooks are
  sent even when notifications are globally disabled.

**V2 target**

```
ProcessCompetitorScan raises → worker catches
  → classify: TransientScrapeError → mark RETRYING, re-enqueue with backoff (bounded)
              PermanentScrapeError → mark FAILED, emit ScrapeFailed event to outbox
  → both paths go through the outbox, so failure alerts obey the same
    enable/disable and delivery guarantees as every other notification
```

---

## Flow 8 — Collection price export (synchronous, user-facing)

Included because it is a second, undocumented path into the scraper.

```
Exports.tsx → collectionPricesExportUrl(...)   builds a URL, browser navigates to it
  → GET /api/exports/collection-prices?competitor_id&collection_url&format&max_pages
    → api/exports.py:39
      → load Competitor (404)
      → _validate_collection_url  ← SSRF guard: host must match competitor host
      → _collection_scrape_payload: forces discover_collections=False,
                                    include_all_products=False,
                                    request_timeout_seconds=8
      → services/scraper.py:51 scrape_competitor(...)      ← live scrape in the request
      → if empty: _saved_collection_products(db, ...)      ← DB fallback (commit f346f70)
      → serialize CSV / JSONL / JSON with Content-Disposition
```

Nothing is persisted and no `ScrapeRun` is recorded, so exports are invisible to the
dashboard and to rate control.
