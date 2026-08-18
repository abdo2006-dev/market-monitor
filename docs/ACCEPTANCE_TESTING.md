# Acceptance Testing

Phase 1F protects the operator's complete daily decision path with two deterministic,
repeatable acceptance layers. Neither layer calls a competitor storefront or Production.

## Backend acceptance

`backend/tests/acceptance/test_acquisition_acceptance.py` exercises the existing
acquisition boundary with committed, scrubbed fixtures and fake sessions. It proves:

- Shopify exact-final-page, empty-terminal-page, and a 1,767-product many-page catalog;
- the 100-page safety ceiling, duplicate-page detection, malformed payload handling, and
  safe partial evidence after a mid-catalog network failure;
- custom-storefront root-products GraphQL cursor handling without collection handles;
- sitemap, Salla cursor, generic observation, identity, price, currency, stock, timestamp,
  completeness, and sanitized-failure contracts;
- the exact 12-storefront diagnostic registry and the runner's database-free boundary.

`test_daily_workflow_acceptance.py` uses migrated disposable PostgreSQL and actual ASGI
routes/use cases. Its morning journey is intentionally sequential:

```text
durable Sync request/claim/reconcile
  → freshness-aware Search comparison
  → explicit cached Export
  → independently acquired live Export
```

It asserts that Search and cached Export reflect committed observations while live Export
contains only the independently acquired request data. It never treats HTTP 202 as a
completed Sync.

Run it with:

```bash
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test \
  .venv/bin/python -m pytest tests/acceptance/ -q
```

## Browser acceptance

`frontend/e2e/acceptance.spec.ts` drives the real React application in Chromium with
deterministic route interception. Projects are desktop 1440×900, tablet 1024×768, and
mobile 390×844. The suite covers:

- keyboard Search selection and separate reliable/degraded/currency evidence;
- out-of-stock and price-change presentation;
- CSV, JSON, and JSONL browser downloads, including Unicode, decimal precision, server
  filename, partial, suspicious-empty, failed-live, and explicit stored states;
- Competitors HTTP 202 truthfulness;
- navigation landmarks, mobile Escape, visible focus treatment, reduced motion, console
  errors, and document overflow.

All mock data uses reserved invalid domains and synthetic values. Screenshots/traces are
safe to retain briefly as CI artifacts. Do not point this suite at Preview or Production.

## Failure ownership

- A deterministic acceptance failure blocks the Preview branch and must be fixed or
  truthfully reported.
- A live coverage warning does not make this suite red. Investigate it using the sanitized
  matrix in `docs/COMPETITOR_COVERAGE.md` and reproduce with the manual runner only.
- Never loosen a trust, provenance, completeness, or HTTP 202 assertion to make a gate
  pass. If intended behavior changes, update its authoritative contract and documentation
  in the same change.
