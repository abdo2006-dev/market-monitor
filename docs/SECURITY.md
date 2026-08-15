# Security

Assessment at baseline `f346f70`. This application currently has **no authentication of
any kind** and is designed for single-operator use. That is a defensible choice for a
personal tool, but it means every item below is reachable by anyone who can reach the URL.

---

## 1. Findings

### 1.1 No authentication or authorization — **High** (by design, but undocumented)

`backend/app/main.py` registers no auth middleware. Every route is public: creating,
editing, and deleting competitors; triggering scans; reading all collected pricing data;
changing settings.

If the Vercel deployment is publicly reachable, an anonymous visitor can trigger unlimited
scans (using your infrastructure to scrape third parties), delete all monitored
competitors, and read the entire dataset.

**Mitigation** Do not expose the deployment publicly, or put it behind Vercel's
deployment protection / an authenticating proxy. Application-level auth is deferred; if it
is added later, it belongs in the API layer per `docs/ARCHITECTURE.md` B-2.

### 1.2 CORS allows all origins with credentials — **High**

`app/main.py:19-25`:

```python
allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
```

`*` with `allow_credentials=True` is a configuration browsers reject for credentialed
requests, so it is currently ineffective rather than actively exploited — but it signals
intent to allow cross-origin credentialed access, and it means any website a user visits
can issue unauthenticated requests to the API from their browser. Combined with 1.1, a
malicious page can silently delete competitors on a locally-running instance.

**Mitigation** Set an explicit origin allowlist. With no cookie-based auth,
`allow_credentials` should be `False`.

### 1.3 Cron endpoints are unauthenticated by default — **High**

`api/cron.py:15`:

```python
def _check_auth(authorization: str | None):
    if settings.CRON_SECRET and authorization != f"Bearer {settings.CRON_SECRET}":
        raise HTTPException(401)
```

The guard is a **no-op when `CRON_SECRET` is unset**, and it defaults to `None`
(`config.py:26`). So `/api/cron/scan-due`, `/api/cron/daily-summary`, and `/api/cron/daily`
are fully public out of the box. `/api/cron/scan-due` runs scans inline, so it is an
unauthenticated way to make the server scrape every competitor on demand.

The comparison is also non-constant-time, which is a minor timing concern relative to the
default-off problem.

**Mitigation** Set `CRON_SECRET` in every deployment. Better: fail closed — refuse to
serve the cron routes at all if the secret is unset. Use `secrets.compare_digest`.

### 1.4 Discord webhook URLs are unprotected credentials — **Medium**

A Discord webhook URL is a bearer credential: anyone holding it can post to that channel.

They are stored as plaintext `String(500)` in `competitors.discord_webhook_url`, returned
in full by `GET /api/competitors` (via `CompetitorOut`), and rendered into the competitor
edit form. With no authentication (1.1), the API hands out every webhook URL to any caller.

**Mitigation** Do not return the webhook in list responses; return a masked form and
accept writes only. Longer term, store credentials separately from the entity.

### 1.5 Shopify Storefront access tokens are harvested from third-party sites — **Medium**, and a legal question rather than a technical one

`services/scraper.py:434-501`. The flow: fetch a competitor's homepage → download up to 40
of their JavaScript bundles → regex out a `*.myshopify.com` domain and a value adjacent to
`X-Shopify-Storefront-Access-Token` → use that token to query their Storefront GraphQL API.

Two distinct concerns:

**Technical.** The fallback pattern is `["']([a-f0-9]{32,})["']` (`:489`) — *any* 32+
character hex string in any downloaded bundle. It will frequently match a build hash, a
cache key, or an unrelated identifier. Those get sent as an auth header to a third party.
Separately, downloading 40 JS bundles per discovery attempt is a meaningful load on
someone else's origin.

**Legal / ToS.** Shopify Storefront tokens are public-by-design for a storefront's own
front end, so this is not a credential *breach*. But extracting a token from someone's
bundle and using it for automated collection from your own infrastructure is a different
use than the one it was issued for, and it is plausibly contrary to the target sites'
terms of service. Whether that is acceptable depends on jurisdiction and on your
relationship with these competitors — it is not a question this document can settle.

**Historical token classification (2026-08-13).** The literal introduced in commit
`9660f6b` was sent as `X-Shopify-Storefront-Access-Token`, which Shopify defines as the
public client-side Storefront token header. Private Storefront tokens use the distinct
`Shopify-Storefront-Private-Token` header. The literal was removed in `8e5f78c`, and
current source contains no hard-coded Storefront token. It is therefore classified
**public**, not a leaked private token; repository-history rewriting solely for secrecy is
not recommended. This classification does not resolve the separate automation/ToS risk.

**Recommendation** Treat this as a decision to make explicitly rather than a default
behaviour. At minimum: make it opt-in per competitor rather than
`auto_discover_storefront_graphql: True` by default (`:445`), and tighten the token pattern
so unrelated hex strings are not transmitted. Record the outcome as an ADR.

### 1.6 SSRF surface — **Low**, currently well handled

`GET /api/exports/collection-prices` takes a caller-supplied URL and fetches it
server-side. `_validate_collection_url` (`exports.py:203`) requires an `http`/`https`
scheme and requires the host to equal the selected competitor's host (both `www.`-stripped).
That is a correct allowlist and it closes the obvious hole.

Residual risk: a competitor's own `base_url` is operator-controlled and unvalidated at
creation, so an operator can point a competitor at an internal host and then export from
it. Given 1.1, "operator" means "anyone". Low severity only because it requires two steps.

`listing_urls` and `selector_config.storefront_graphql.shop_domain` are fetched during
scans with no host validation at all.

**Mitigation** Validate `base_url` on write: public scheme, resolvable public IP, reject
loopback/link-local/private ranges.

### 1.7 Scraper input is parsed with regex over untrusted HTML/JS — **Low**

`scraper.py` runs many regexes over attacker-influenced content (`:458`, `:477`, `:489`,
`:586`, `:751`, `:759`, `:768`, `:775`). None appear catastrophically backtrackable, and
timeouts bound the fetch, but not the parse. A hostile competitor site could serve a
pathological page. Content is also size-unbounded — `await resp.text()` on a multi-GB
response would exhaust memory.

**Mitigation** Cap response size before parsing.

### 1.8 Error messages are echoed to clients and to Discord — **Low**

`tasks.py:137` stores `str(e)[:1000]` in `ScrapeRun.error_message`; `:154` sends
`str(e)` to Discord; `scan_now` returns the scan result including error text to the HTTP
caller. Exception strings can contain full URLs with query parameters, and in a
connection error, internal hostnames.

**Mitigation** Log the detail; return and notify a classified summary.

### 1.9 `SECRET_KEY` is unused — **Informational**

Declared (`config.py:8`), documented in `.env.example` with generation instructions, and
read by nothing. There is no session, token, or signing mechanism. It is currently
harmless, but it gives a false impression that something is being signed.

### 1.10 Repository hygiene — **resolved in Phase 0**

- No `.env` file is tracked. `.env.example` contains no real values. Correct at baseline.
- `.DS_Store`, `backend/.DS_Store`, and six `__pycache__/*.pyc` files **were** tracked
  despite matching `.gitignore` (added before the ignore rules). Untracked in Phase 0.
- `.vercel/` is correctly ignored and untracked.
- No database dumps, browser profiles, or credentials found anywhere in the tree.

### 1.11 V2 user-test Preview fixture — **isolated and default-off**

The user-testing Preview uses a separate Neon project and Vercel Deployment Protection.
Deterministic acquisition is enabled only when `PREVIEW_DEMO_MODE=true`, Vercel's reserved
`VERCEL_ENV` is exactly `preview`, and the selected competitor row carries
`selector_config.preview_demo=true`. Production cannot activate the adapter by setting the
feature flag alone. Fixture hosts use the reserved `.invalid` suffix, notifications and
automatic Sync are disabled, and no Preview worker is connected. See
`docs/PREVIEW_TESTING.md` for the complete isolation evidence and test data.

---

## 2. Data handled

- **Competitor storefront data**: public product listings, prices, stock, images. Public
  by nature.
- **Discord webhook URLs**: credentials. See 1.4.
- **Shopify Storefront tokens**, when discovered: third-party credentials held in
  `selector_config` JSON and therefore returned by `GET /api/competitors`. See 1.5.
- **No end-user personal data.** No accounts, no customer records, no PII. This
  meaningfully limits the blast radius of everything above.

---

## 3. Rules for contributors

- Never commit `.env`, `.env.local`, webhook URLs, access tokens, or database dumps.
- Never put a real webhook URL or access token in a test fixture. Scrub captured fixtures
  before committing them.
- Never log a webhook URL or a token.
- Do not add a new server-side fetch of a user-supplied URL without an allowlist check
  modelled on `_validate_collection_url`.
- Treat everything returned by a scrape as hostile input. It is authored by a third party.
- Set `CRON_SECRET` in every deployment.

---

## 4. Recommended order of remediation

1. `CRON_SECRET` — fail closed when unset (1.3). Small change, removes an unauthenticated
   scan trigger.
2. CORS allowlist (1.2). Small change.
3. Mask webhook URLs in API responses (1.4).
4. Decide explicitly on Storefront token discovery (1.5) and record an ADR.
5. Validate `base_url` on write (1.6).
6. Bound response sizes in the scraper (1.7).

Authentication (1.1) is deliberately not on this list: it is a product decision, not a
defect to be fixed silently. If the deployment is or becomes public, it moves to the top.
