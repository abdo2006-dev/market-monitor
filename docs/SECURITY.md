# Security

The baseline assessment was performed at `f346f70`. Phase 1G adds a deliberately small
single-operator production access gate. It is not a multi-user identity system: one
server-side password hash establishes a signed, expiring browser session, and every data
or mutation API remains inaccessible without that session.

---

## 1. Findings

### 1.1 Production application access — **resolved for the single-operator topology**

`app.auth.SingleUserAuthMiddleware` protects all API data and mutation routes when
`APP_AUTH_ENABLED=true`. The only public application endpoints are `/health`, auth status,
and login. Cron routes use their separate bearer guard. The SPA mounts no workspace route
until auth status proves an active session, and a lock action clears it.

The password is stored only as PBKDF2-SHA256 (600,000 iterations); an independent secret
signs a 12-hour default session cookie. The cookie is `Secure`, `HttpOnly`, `SameSite=Strict`,
and scoped to `/`. Rotating either the password hash or signing secret invalidates existing
sessions. State-changing browser calls also require the fixed non-simple
`X-Market-Monitor-CSRF` header and a same-origin `Origin` when supplied. Missing or malformed
auth configuration fails closed. No GitHub/cron/database credential is sent to the browser.

Vercel Standard Protection remains appropriate for Preview. The current Hobby plan does
not protect the Production domain, which is why Production must set the application gate.

### 1.2 Cross-origin browser access — **resolved**

`app/main.py` now configures no cross-origin allowlist, credentials, methods, or headers:

```python
allow_origins=[], allow_credentials=False, allow_methods=[], allow_headers=[]
```

Production uses one origin for UI and API. Local development should use Vite's `/api`
proxy rather than a cross-origin `VITE_API_URL`.

### 1.3 Cron bearer authentication — **resolved**

`api/cron.py:15`:

```python
def _check_auth(authorization: str | None):
    expected = f"Bearer {settings.CRON_SECRET}" if settings.CRON_SECRET else None
    if expected is None or authorization is None or not secrets.compare_digest(...):
        raise HTTPException(401)
```

The routes now return 401 when `CRON_SECRET` is absent and compare configured bearer values
in constant time. Production still keeps Vercel morning Sync disabled; GitHub is the one
automatic owner only after the manual rollout proof.

### 1.4 Discord webhook URLs are unprotected credentials — **Medium**

A Discord webhook URL is a bearer credential: anyone holding it can post to that channel.

They are stored as plaintext `String(500)` in `competitors.discord_webhook_url`, returned
in full by `GET /api/competitors` (via `CompetitorOut`), and rendered into the competitor
edit form. Phase 1G prevents anonymous access, but masking write-only credentials remains
the better API design.

**Mitigation** Do not return the webhook in list responses; return a masked form and
accept writes only. Longer term, store credentials separately from the entity.

### 1.5 Shopify Storefront access tokens are harvested from third-party sites — **Medium**, and a legal question rather than a technical one

`services/scraper.py:434-501`. The flow: fetch a competitor's homepage → download up to 40
of their JavaScript bundles → regex out a `*.myshopify.com` domain and a value adjacent to
`X-Shopify-Storefront-Access-Token` → use that token to query their Storefront GraphQL API.

Two distinct concerns:

**Technical.** Phase 1F added a narrow Vite/minified-client pattern: a public Shopify
GraphQL endpoint must be immediately followed by a quoted 20–128-character token-like
assignment. This enabled the platform-level root-products fallback without hardcoding a
store token. The older fallback pattern `["']([a-f0-9]{32,})["']` still exists after the
header-adjacent checks and can match a build hash, cache key, or unrelated identifier.
Separately, downloading up to 40 JS bundles per discovery attempt is a meaningful load on
someone else's origin. Discovery values are never logged, stored in telemetry, fixtures,
the coverage artifact, or committed source.

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

### 1.9 Legacy `SECRET_KEY` remains unused — **Informational**

The new session deliberately uses the explicit `APP_AUTH_SESSION_SECRET`; it does not
silently repurpose the older generic `SECRET_KEY`.

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

1. Mask webhook URLs in API responses (1.4).
2. Decide explicitly on Storefront token discovery (1.5) and record an ADR.
3. Validate `base_url` on write (1.6).
4. Bound response sizes in the scraper (1.7).
