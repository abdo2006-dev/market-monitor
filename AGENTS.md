# AGENTS.md — Instructions for coding agents working on Market Monitor

This file is the canonical operating manual for Claude, Codex, and any other coding agent
working in this repository. Read it fully before making substantial changes.

The project is currently at the **protected V2 Preview and owner-testing gate**. Phase 0
produced an audit and target architecture; Phase 1A made migrations safe; Phase 1B.1
enforced product identity and concurrent reconciliation safety; Phase 1B.2 added the
durable Sync lifecycle; Phase 1C completed trustworthy Search; Phase 1D completed truthful
Exports; and the protected Preview now carries the shared UI system. Read
`docs/PROJECT_STATUS.md` first — it tells you where the work actually stands today.

---

## 0. Product priority — read before choosing what to work on

The owner uses three workflows **every day** and makes real pricing decisions from them:

1. **Market Search** (`/search`) 2. **Collection Exports** (`/exports`) 3. **Competitor
price synchronisation**

Priority order: **P0** database/migration safety · **P1** Sync reliability · **P2** Search
reliability and freshness · **P3** Export reliability and provenance · **P4** UX for those
three · *then* other functionality (Dashboard, Activity, Discord, Settings) · *then*
Treasury Audit.

**Do not spend effort below the line while anything above it is unreliable**, and do not
improve unrelated features "while you are in there". Full detail:
`docs/DAILY_CRITICAL_WORKFLOWS.md`.

The governing principle: *do not optimise architecture for elegance while the daily
workflows remain unreliable.*

---

## 1. Required reading before substantial work

In this order:

1. `docs/PROJECT_STATUS.md` — current phase, branch, baseline, blockers, next task.
2. `AGENTS.md` (this file) — the rules.
3. `docs/DAILY_CRITICAL_WORKFLOWS.md` — the three workflows, their failure modes, and their
   regression coverage.
4. `docs/ARCHITECTURE.md` — the V2 target architecture and its layer boundaries.
5. `docs/CURRENT_SYSTEM.md` — what actually exists today, with file references.
6. Any ADR in `docs/adr/` relevant to what you are touching.

For scraping work also read `docs/SCRAPING_ARCHITECTURE.md`.
For anything that crosses the HTTP boundary also read `docs/API_CONTRACTS.md`.
Before any schema or migration work, read `docs/RUNBOOK.md` §2.2.

Do not skip this because a task "looks small". Several behaviours in this codebase are
implemented in more than one place (see §4), and a small change in the wrong copy
produces a silent inconsistency rather than an error.

---

## 2. Inspect before you modify

- Read the existing implementation before changing it. Do not infer behaviour from the
  README, from a function name, or from this document alone — the source is the truth.
- Before adding a function, grep for an existing one. This repository already contains
  several near-duplicate helpers (for example collection-alias matching exists in both
  `backend/app/api/search_dashboard_settings.py` and `backend/app/api/exports.py`).
- Before changing behaviour, identify the **authoritative owner** of that behaviour and
  change it there. If there is currently no single owner, say so in your report rather
  than picking one silently.

---

## 3. Architectural boundaries

The V2 target is a **modular monolith** (see `docs/adr/0001-modular-monolith.md`). The
intended layering is:

```
api/          HTTP transport only: validation, auth, serialization
application/  use cases: RequestCompetitorScan, ProcessCompetitorScan, ...
domain/       business rules and entities, framework-free where practical
infrastructure/  repositories, scraper adapters, notifiers, queue, HTTP clients
workers/      Celery entrypoints that invoke application use cases
```

Rules:

- **API routes must not orchestrate scans.** V2 routes call `app.application.sync` request
  or status use cases. The old direct implementations exist only inside explicit
  `SYNC_EXECUTION_MODE=legacy` rollback branches; do not extend them.
- **The API must never import private worker internals.** Importing an underscore-prefixed
  function from `app.workers.tasks` into a route is prohibited. Both call sites that do
  this today are recorded in `docs/PROJECT_STATUS.md` as known issues.
- **Do not duplicate orchestration between layers.** In particular, do not implement
  scan fan-out, retry, or concurrency control in React. The frontend requests work; the
  backend decides how it runs.
- **Do not bypass a boundary for convenience.** If a use case needs data it cannot reach,
  extend the interface — do not reach into a repository or a Celery task directly.
- **External integrations go behind adapters.** New scraping platforms, notification
  channels, and blockchain providers are adapters implementing a documented interface,
  not new branches inside an existing function.
- **Substantial UI work follows `docs/UI_UX_SYSTEM.md`.** Reuse its tokens, shell,
  status language, responsive strategy, loading/error patterns, and daily-workflow
  composition. Do not add page-specific shell hacks or a second visual system.

---

## 4. Behaviours that currently exist in more than one place

Be careful with these. Changing one copy and not the other is the most likely way to
introduce a silent bug here.

| Behaviour | Locations |
|---|---|
| V2 Sync request/process | `application/sync.py` is authoritative; compatibility routes delegate under `SYNC_EXECUTION_MODE=v2` |
| Legacy scan execution | rollback-only branches in `api/competitors.py`, `api/cron.py`, and `workers/tasks.py`; do not add consumers |
| Non-terminal run ownership | PostgreSQL partial unique index + request advisory lock + claim fencing in `application/sync.py` |
| Collection alias matching | `api/search_dashboard_settings.py:790`, `api/exports.py:184` |
| Roblox game/category vocabulary | `services/scraper.py:926`, `api/search_dashboard_settings.py:56-93`, `api/exports.py:170` |

---

## 5. Database rules

- **Alembic is the single schema authority.** As of Phase 1A the application never creates
  or alters schema at runtime — startup only *verifies*. Do not reintroduce
  `create_all`, `drop_all`, or ad-hoc DDL; `test_application_startup_never_creates_schema`
  will fail if you do (it parses the AST, so prose mentioning `create_all` is fine).
- **Schema changes require an Alembic migration.** No exceptions.
- After changing a model, run autogenerate against a migrated database and confirm the diff
  is empty or exactly what you intended. **The CI drift check is a required gate** — a
  model/migration disagreement fails the build.
- Migrations must be reversible where practical; write a real `downgrade()`.
- **Never `alembic stamp` a database without classifying it first.** Run
  `backend/scripts/check_schema_state.py` (read-only) and follow `docs/RUNBOOK.md` §2.2.
  Stamping a Case C (genuinely drifted) database tells Alembic a lie that every later
  migration inherits. Take a backup before anything that writes.
- **Do not run destructive production migrations automatically.** The manual workflow
  `.github/workflows/db-migrate.yml` is the supported path; it defaults to read-only.
- PostgreSQL is the durable source of truth. Redis is a broker and a cache, never the
  system of record.

---

## 6. API contract rules

- A change to a response shape is a breaking change until proven otherwise. The frontend
  consumes these responses untyped in several places, so the compiler will **not** catch
  it for you.
- When you change a route's request or response, update `docs/API_CONTRACTS.md` and the
  corresponding frontend call site in `frontend/src/lib/api.ts` in the same change.
- Prefer declaring an explicit Pydantic `response_model`. Several routes today return bare
  `dict`, which means FastAPI's OpenAPI schema does not describe them — this is why
  contract generation is a Phase 1 task, not a Phase 0 one.
- Do not widen a type to `Any`/`any` to make an error go away.

---

## 7. Testing rules

- **Do not weaken a test to make it pass.** Do not delete assertions, loosen a comparison,
  add a broad `pytest.mark.skip`, or wrap a failing call in `try/except` to get green.
  If a test fails, find the cause and report it.
- Bug fixes require a regression test whenever the bug is reachable from a unit or
  integration test.
- Scraper tests and required CI gates must use committed fixtures or mocks. **Never** put a
  request to a live competitor storefront in pytest, Playwright, a push/PR workflow, or any
  blocking release check. Live coverage belongs only in the manual, observational,
  database-free `competitor-coverage-smoke.yml` path documented in
  `docs/COMPETITOR_COVERAGE.md`; its warnings are evidence for operator review, not a
  deterministic test failure.
- **Characterisation tests are not aspirational.** Several tests in `tests/critical/`
  deliberately assert *current, defective* behaviour so that fixing it is a visible change
  — for example `test_concurrent_scans_of_one_competitor_are_not_prevented` asserts that
  duplicate products ARE created. When you fix the defect, **invert the assertion and
  update the docs**; do not delete the test.
- Run the relevant checks before you report completion:

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:5432/market_monitor_test .venv/bin/python -m pytest tests/ -q
```

```bash
cd frontend && npx tsc --noEmit && npm run build
```

- Database-backed tests **skip** without `TEST_DATABASE_URL`. A run reporting only 65 tests
  means the critical suite did not execute — that is not a pass.

- If a check cannot be run in your environment (for example anything needing PostgreSQL
  or Redis), say so explicitly in your report. Do not describe an unrun check as passing.

---

## 8. Documentation rules

- Update documentation in the same change as the code it describes.
- Update `docs/PROJECT_STATUS.md` after any substantial task — it is the handoff file
  between agent sessions, and a stale one is worse than none.
- Record major architectural decisions as an ADR in `docs/adr/` using the existing
  template (Context / Decision / Alternatives considered / Consequences / Status).
- Do not create placeholder or aspirational documentation. Every document in `docs/`
  must describe the repository as it actually is, or be clearly marked as a target design.
- If you discover that a document is wrong, fix the document as part of your task.

---

## 9. Security and privacy

- **Never commit secrets**: no `.env`, no API keys, no webhook URLs, no database dumps,
  no browser profiles, no local databases. `.env`, `.env.local`, and `.env.*.local` are
  gitignored — keep it that way.
- Discord webhook URLs are credentials. They live in the database and in environment
  variables; never log them, never put one in a fixture, never put one in a doc.
- Do not add real competitor storefront responses containing personal data to fixtures.
  Trim fixtures to the fields the parser actually reads.
- Read `docs/SECURITY.md` before touching authentication, CORS, the cron endpoints, or
  the Shopify Storefront token discovery path in `services/scraper.py:476`.

---

## 10. Git rules

- **Preserve the existing Git author identity.** Do not change `user.name` or
  `user.email`.
- **Do not add AI tools or models as Git contributors.** No `Co-authored-by:` lines
  naming Claude, Codex, GPT, or any assistant. No "Generated with" trailers in commits.
- Do not force-update or delete `archive/pre-v2-rearchitecture` or any other archive tag.
- Do not commit `.DS_Store`, `__pycache__/`, `*.pyc`, `node_modules/`, or `dist/`.
- Work on a feature branch. The completed Phase 1B.2 release branch is
  `v2/durable-sync-lifecycle`; the completed Phase 1C branch is `v2/search-trust-ui`.
  Create a focused Phase 1D branch rather than working on `main`.

---

## 11. Scope discipline

This is primarily a personal-use application. Reliability and clear boundaries matter;
scale theatre does not. **Do not introduce**: Kubernetes, Kafka, a service mesh, event
sourcing as a global pattern, multiple deployable services, or an enterprise IAM system.
Do not add a dependency without stating in your report what it does and why an existing
dependency cannot.

If you believe a large change is warranted, write an ADR proposing it with `Status:
Proposed` and stop. Do not implement it in the same pass.

---

## 12. Definition of done

Before you report a task complete:

1. Review your complete diff, file by file.
2. Run the checks in §7 and paste the real output.
3. Confirm no existing user-facing feature was removed or changed unintentionally.
4. Confirm documentation matches the code you just wrote.
5. Confirm no secret or private data entered the repository.
6. Update `docs/PROJECT_STATUS.md`.

Report honestly. If something is partially done, blocked, or unverified, say which part
and why. A truthful partial result is more useful than an optimistic summary.
