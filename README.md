# 📡 Market Monitor

A self-hosted, private competitor price-monitoring system for e-commerce businesses. Track competitor products, detect price changes, receive Discord alerts, and search the market from a clean dashboard.

---

## What It Does

- **Monitors competitor websites** on a configurable schedule using Playwright
- **Detects changes**: new products, price increases/decreases, stock changes, removed products
- **Stores full history** — every product snapshot is saved by date
- **Discord notifications** — rich embeds for every meaningful event
- **Market search** — type a product name and instantly compare prices across all competitors
- **Activity feed** — grouped by date with filters
- **Dashboard** — live summary of today's events

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI + Python 3.12 |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2 (async) |
| Migrations | Alembic |
| Scraping | Playwright (Chromium) |
| Task Queue | Celery + Redis |
| Frontend | React 18 + Vite + TypeScript |
| Charts | Recharts |
| Deployment | Docker Compose |

---

## Quick Start (Docker)

### 1. Clone and configure

```bash
git clone https://github.com/you/market-monitor.git
cd market-monitor
cp .env.example .env
# Edit .env if needed (defaults work for local Docker setup)
```

### 2. Start everything

```bash
docker compose up --build
```

This starts:
- **PostgreSQL** on port 5432
- **Redis** on port 6379
- **Backend API** on port 8000 (runs migrations automatically)
- **Celery Worker** — executes scrape jobs
- **Celery Beat** — schedules recurring scans
- **Frontend** on port **3000** ← open this in your browser

### 3. Open the dashboard

```
http://localhost:3000
```

---

## Manual / Development Setup

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# Start PostgreSQL and Redis (e.g. via Docker):
docker run -d -p 5432:5432 -e POSTGRES_USER=market -e POSTGRES_PASSWORD=market -e POSTGRES_DB=market_monitor postgres:16
docker run -d -p 6379:6379 redis:7

# Copy and edit .env
cp ../.env.example .env

# Run migrations
alembic upgrade head

# Start API server
uvicorn app.main:app --reload --port 8000

# In another terminal — start Celery worker
celery -A app.workers.celery_app worker --loglevel=info

# In another terminal — start Celery beat scheduler
celery -A app.workers.celery_app beat --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
# Create .env.local if API is not on default port:
echo "VITE_API_URL=http://localhost:8000/api" > .env.local
npm run dev
# Open http://localhost:3000
```

---

## Adding Your First Competitor

1. Click **Competitors** in the sidebar
2. Click **+ Add Competitor**
3. Fill in:
   - **Name**: Human-readable label (e.g. "Nike Store")
   - **Base URL**: Root domain (e.g. `https://www.nike.com`)
   - **Listing URLs**: One URL per line — these are the pages that list products (e.g. category pages, sale pages)
   - **Scan Frequency**: How often to re-scan (in minutes)
   - **Selector Config**: CSS selectors for this site (see below)
   - **Discord Webhook** *(optional)*: Paste your webhook URL for per-competitor alerts
4. Click **Add Competitor**
5. Click **▶ Scan** to run the first scan immediately

---

## How selector_config Works

The `selector_config` JSON tells the scraper which CSS selectors to use on that competitor's product listing pages.

```json
{
  "product_card": ".product-card",
  "title": ".product-title",
  "price": ".price",
  "url": "a.product-link",
  "image": "img.product-image",
  "stock": ".stock-status",
  "pagination_next": "a.pagination-next"
}
```

| Field | Description |
|-------|-------------|
| `product_card` | **Required.** Selector for each product's container element |
| `title` | Selector for the product title (within the card) |
| `price` | Selector for the price text (within the card) |
| `url` | Selector for the product link `<a>` (within the card) |
| `image` | Selector for the product image (within the card) |
| `stock` | Selector for stock status text (within the card) |
| `pagination_next` | Selector for the "Next page" link — omit if no pagination |

### Tips for finding selectors

1. Open the competitor's product listing page in Chrome
2. Right-click a product card → **Inspect**
3. Find the common wrapper class for all products
4. Repeat for title, price, etc.

The scraper follows pagination automatically up to `default_max_pages` (configurable in Settings).

---

## How Discord Notifications Work

Each competitor can have its own Discord webhook URL. When a scan detects changes, notifications are sent for:

- 🆕 **New product found** — shows title, price, stock, link
- 📉 **Price decreased** — shows old/new price, difference amount & percentage
- 📈 **Price increased** — same format
- 📦 **Stock changed** — in stock / out of stock
- ❌ **Scan failed** — competitor name and error message
- 📊 **Daily summary** — sent every morning with totals

**Rate limiting:** The system pauses 1 second between webhook calls to respect Discord's limits.

**To get a webhook URL:**
1. Open Discord → Server Settings → Integrations → Webhooks
2. Create a new webhook, assign it to a channel
3. Copy the webhook URL and paste it into the competitor form

---

## How to Run a Manual Scan

Three ways:
1. **Dashboard** → Competitors section → click **▶ Scan** next to any competitor
2. **Competitors page** → click **▶ Scan** button
3. **API directly**: `POST /api/competitors/{id}/scan-now`

Manual scans are queued to Celery and run asynchronously. The scan status updates within seconds.

---

## API Reference

All endpoints are prefixed with `/api`.

### Competitors
```
GET    /api/competitors              List all competitors
POST   /api/competitors              Create a competitor
GET    /api/competitors/{id}         Get one competitor
PUT    /api/competitors/{id}         Update a competitor
DELETE /api/competitors/{id}         Delete a competitor
POST   /api/competitors/{id}/scan-now  Trigger immediate scan
```

### Products
```
GET    /api/products                 List products (filterable)
GET    /api/products/{id}            Get one product
GET    /api/products/{id}/history    Get price/snapshot history
```

Query params for `/api/products`:
`competitor_id`, `search`, `active`, `stock_status`, `min_price`, `max_price`, `date_from`, `date_to`, `sort`, `page`, `page_size`

### Events
```
GET    /api/events                   List events (filterable)
```

Query params: `event_type`, `competitor_id`, `date_from`, `date_to`, `notification_sent`

### Search
```
GET    /api/search/products?q=...    Search across all competitors
```

### Dashboard & Settings
```
GET    /api/dashboard/summary        Today's stats + latest events
GET    /api/settings                 App settings
PUT    /api/settings                 Update settings
```

Interactive docs: `http://localhost:8000/docs`

---

## Running Tests

```bash
cd backend
pip install pytest pytest-asyncio
pytest tests/ -v
```

Tests cover:
- Price parser (12 scenarios: currency symbols, European format, thousands separators)
- Title normalizer (casing, punctuation, unicode)
- URL normalizer (relative → absolute)
- Change detection (price event types, price comparison logic)
- Scraper stock detection

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL asyncpg URL |
| `REDIS_URL` | — | Redis connection URL |
| `CELERY_BROKER_URL` | — | Celery broker (same as Redis) |
| `CELERY_RESULT_BACKEND` | — | Celery results backend |
| `SECRET_KEY` | — | App secret (use a random 32-byte hex) |
| `DEFAULT_TIMEZONE` | `UTC` | Display timezone |
| `DEFAULT_CURRENCY` | `USD` | Fallback currency for price parsing |
| `DISCORD_NOTIFICATIONS_ENABLED` | `true` | Master switch for Discord |
| `USER_AGENT` | `MarketMonitor/1.0` | HTTP user-agent string |
| `PLAYWRIGHT_HEADLESS` | `true` | Run browser headless |

---

## Project Structure

```
market-monitor/
├── backend/
│   ├── app/
│   │   ├── main.py                  FastAPI app entry point
│   │   ├── config.py                Settings (pydantic-settings)
│   │   ├── database.py              Async SQLAlchemy engine
│   │   ├── models/__init__.py       All ORM models
│   │   ├── schemas/__init__.py      Pydantic request/response schemas
│   │   ├── api/
│   │   │   ├── competitors.py       CRUD + scan-now
│   │   │   ├── products.py          Products + history
│   │   │   ├── events.py            Event log
│   │   │   └── search_dashboard_settings.py
│   │   ├── services/
│   │   │   ├── scraper.py           Playwright generic scraper
│   │   │   ├── detection.py         Change detection logic
│   │   │   └── notification.py      Discord webhook sender
│   │   ├── workers/
│   │   │   ├── celery_app.py        Celery app + beat schedule
│   │   │   └── tasks.py             Scrape task + scheduler
│   │   └── utils/
│   │       ├── price_parser.py      Robust price parsing
│   │       └── text_normalizer.py   Title normalization
│   ├── alembic/                     Database migrations
│   ├── tests/test_core.py           Test suite
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   └── src/
│       ├── App.tsx                  Router
│       ├── lib/
│       │   ├── api.ts               Axios API client
│       │   └── utils.ts             Formatting helpers
│       ├── components/
│       │   ├── ui/index.tsx         Shared UI components
│       │   ├── layout/Sidebar.tsx   Navigation sidebar
│       │   └── CompetitorForm.tsx   Add/edit competitor modal
│       └── pages/
│           ├── Dashboard.tsx        Home dashboard
│           ├── Competitors.tsx      Competitor management
│           ├── Products.tsx         Product list with filters
│           ├── ProductDetail.tsx    Price chart + history
│           ├── MarketSearch.tsx     Cross-competitor search
│           ├── Activity.tsx         Event feed by date
│           └── Settings.tsx         App configuration
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Troubleshooting

**Playwright can't find Chromium:**
```bash
playwright install chromium
playwright install-deps chromium  # Linux only
```

**Migrations fail to connect:**
Make sure `DATABASE_URL` uses `postgresql+asyncpg://` (not `postgresql://`).

**Celery workers not picking up tasks:**
Confirm Redis is running and `CELERY_BROKER_URL` matches. Check worker logs with `--loglevel=debug`.

**Products not being found during scan:**
- Open the listing URL in a browser and inspect the HTML
- Check that your `product_card` selector matches actual elements
- Try a more general selector (e.g. `article`, `.item`, `[data-product]`)
- Some sites lazy-load content — try adding `await page.wait_for_load_state('networkidle')` (requires scraper modification)

**Discord notifications not sending:**
- Verify the webhook URL is correct (starts with `https://discord.com/api/webhooks/`)
- Check `DISCORD_NOTIFICATIONS_ENABLED=true` in your `.env`
- Look for errors in the worker logs

---

## Ethical Scraping Notes

Market Monitor is designed for **monitoring publicly available pricing information** from websites you have a legitimate business interest in monitoring. Please use it responsibly:

- **Only scrape public URLs** — the tool does not bypass logins, CAPTCHAs, or paywalls
- **Respect rate limits** — use reasonable scan intervals (30+ minutes) and page delays (2+ seconds)
- **Check robots.txt** — review whether the sites you monitor permit automated access
- **Don't overload servers** — limit `max_pages` and use delays between requests
- **Use for your own competitive intelligence only** — do not resell or redistribute scraped data
- The built-in `USER_AGENT` setting lets you identify your bot in requests

This tool is intended for small business owners monitoring a handful of competitors — not for large-scale scraping operations.

---

## License

MIT — use freely, commercially or otherwise. No warranty.
