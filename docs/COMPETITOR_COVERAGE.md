# Competitor Coverage

Phase 1F introduces one canonical, non-sensitive registry and a gentle read-only smoke
runner for the 12 owner-specified storefronts. This is operational observation, not a
second competitor database and not deterministic CI.

## Canonical matrix

| Competitor | Public base URL | Expected family | Known caveat |
|---|---|---|---|
| Zyron | `https://zyron.gg/` | Shopify | — |
| Bloxloot | `https://bloxloot.gg/` | Shopify | — |
| TubbysTubby | `https://tubbystubby.com/` | Salla | Historical hostname was `tubbyshtubby.com`; the owner-provided canonical host is tested truthfully. |
| Bloxshop | `https://bloxshop.org/` | Shopify | — |
| BloxyBarn | `https://www.bloxybarn.gg/` | Shopify | — |
| MM2Cheap | `https://mm2.cheap/` | Shopify | — |
| Shopbloxs | `https://shopbloxs.com/` | Shopify custom storefront | Root-products Storefront GraphQL is expected when `products.json` is unavailable. |
| Luger.GG | `https://luger.gg/` | Shopify | — |
| BuyBlox | `https://buyblox.gg/` | Shopify | — |
| PetPatch.GG | `https://petpatch.gg/` | Shopify | — |
| BloxCrew | `https://bloxcrews.com/` | Shopify custom storefront | GraphQL or sitemap fallback may be required. |
| Bloxy Store | `https://bloxystores.com/` | Shopify | — |

The source is `backend/app/diagnostics/coverage_registry.py`. It contains public URLs,
strategy hints, and caveats only—no cookies, tokens, webhooks, database URLs, selectors
containing credentials, or private response bodies.

## Run locally

```bash
cd backend
.venv/bin/python -m app.diagnostics.live_coverage \
  --json /tmp/market-monitor-coverage.json
```

Useful bounded options are `--only NAME`, `--max-pages 1..100`, `--timeout-seconds 1..900`,
and `--previous PATH` for a count-drop comparison. The default ceiling is 100 pages. The
runner is sequential and calls `acquire_catalog()` directly; it imports no model, creates
no database session, dispatches no Sync request, and cannot write Product, Snapshot,
Event, ScrapeRun, or SyncRequest state.

The manual GitHub workflow `.github/workflows/competitor-coverage-smoke.yml` provides the
same observational run and a 14-day sanitized JSON artifact. It has only
`workflow_dispatch`, `contents: read`, pinned actions, no Production environment, no
secrets, no database URL, and no schedule/PR/push trigger.

## Result interpretation

| Result | Meaning |
|---|---|
| `HEALTHY` | Non-empty acquisition, expected strategy, complete evidence, acceptable price and identity signals. |
| `DEGRADED` | Products were acquired, but strategy, price coverage, identity, uniform-price/stock, or prior-count evidence needs review. |
| `PARTIAL` | Useful products were acquired without proof of catalog completion. Absence inference remains disabled. |
| `SUSPICIOUS_EMPTY` | A normally non-empty storefront produced zero products without trustworthy absence evidence. |
| `FAILED` | Acquisition produced a safe failure category or timed out. |

The artifact records only strategy names, aggregate counts, currencies, completeness,
request/status counts, duration, safe warnings, and safe failure categories. It never
stores response bodies, request URLs, headers, exception messages, credentials, or raw
provider payloads. File mode is set to `0600` locally.

Warnings are non-blocking by design because external storefronts change independently of
the repository. Use `--fail-on-unhealthy` only for a deliberate, attended release audit;
the manual workflow intentionally omits it. Any fix must be platform-level and
fixture-backed—do not add one-off per-store parsing branches when an existing adapter
family can own the behavior.

## Latest attended evidence — 2026-08-18

The complete sequential matrix plus focused post-fix reruns produced this current verdict:

| Competitor | Products | Price coverage | Strategy | Completeness | Result |
|---|---:|---:|---|---|---|
| Zyron | 612 | 100% | products.json (aiohttp) | complete | HEALTHY |
| Bloxloot | 2,363 | 100% | products.json (aiohttp) | complete | HEALTHY |
| TubbysTubby | 0 | — | Salla | failed | FAILED |
| Bloxshop | 549 | 100% | products.json (aiohttp) | complete | HEALTHY |
| BloxyBarn | 888 | 100% | products.json (aiohttp) | complete | HEALTHY |
| MM2Cheap | 395 | 100% | products.json (aiohttp) | complete | HEALTHY |
| Shopbloxs | 534 | 100% | Storefront GraphQL root products | complete | HEALTHY |
| Luger.GG | 2,547 | 100% | products.json (aiohttp) | complete | HEALTHY |
| BuyBlox | 2,598* | 100% | products.json (public Shopify endpoint) | complete | NETWORK-SPECIFIC |
| PetPatch.GG | 252 | 100% | products.json (aiohttp) | complete | HEALTHY |
| BloxCrew | 1,067 | 100% | Storefront GraphQL | complete | HEALTHY |
| Bloxy Store | 345 | 100% | products.json (aiohttp) | complete | HEALTHY |

All ten standard-runner healthy rows had zero duplicate external-identity and canonical-URL
evidence. No 429 or 5xx response was observed. TubbysTubby's canonical hostname currently
serves a coming-soon portfolio rather than a catalog; it is therefore an inactive
competitor, with historical products retained rather than inferred absent.

`*` The ordinary local BuyBlox attempt failed safely as `temporary_network`. The local DNS
resolver returned `195.71.232.240` with a Whalebone Sinkhole certificate, while Google and
Cloudflare public DNS returned Shopify's `23.227.38.65`. A focused, hostname-verified request
to that public Shopify address paged 10 full pages of 250 plus a final page of 98: 2,598
unique products and 100% valid variant prices. Certificate verification remained enabled;
no application TLS bypass or store-specific parser branch was introduced. Production-runner
evidence is still required because the production topology, not a local DNS override, is
the release authority.
