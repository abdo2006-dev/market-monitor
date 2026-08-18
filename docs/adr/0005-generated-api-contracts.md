# ADR 0005 — Generated and CI-verified frontend API contracts

**Status:** Accepted (Phase 0, 2026-08-10)

## Context

The Python↔TypeScript boundary is entirely unchecked.

- **14 of 26 application routes declare no `response_model`** (generated from the live app;
  see `docs/API_CONTRACTS.md` §1). FastAPI therefore documents them in `openapi.json` as
  untyped objects.
- `frontend/src/lib/api.ts` — the single, correctly-centralized API module — has **no
  return type on any of its 20 exports**, and 12 take `any` parameters.
- 47 `any` occurrences across 10 frontend files.
- `POST /competitors/{id}/scan-now` returns two different shapes depending on
  `scrape_type`, and can return `{"result": null}` for a scan that never ran. The frontend
  copes with optional chaining rather than types.
- 422 validation errors return `detail` as a **list of objects** while `HTTPException`
  returns it as a **string**; `api.ts:31` renders it directly, so validation errors render
  as `[object Object]`.
- **`npx tsc --noEmit` fails at baseline** (`SalesTrends.tsx:218`, TS2550 `replaceAll`)
  while `npm run build` passes, because Vite transpiles with esbuild and never typechecks.
  A type error has been shipping to production.
- `package.json` declares a `lint` script but eslint is not installed, so it has never run.

The net effect: a backend response change is a runtime failure in a React component, and
nothing in the toolchain would notice.

## Decision

Make the API contract machine-checked, in this order — each step is a prerequisite for the
next.

**1. Make `tsc --noEmit` pass and gate it in CI.** Nothing else is enforceable until the
typecheck is green and running. *(Done in Phase 0.)*

**2. Declare an explicit `response_model` on all 14 routes that lack one.** Model the
shapes that already exist; do not change them. Verified route by route by capturing the
current JSON response, adding the model, and asserting the response is unchanged.

**3. Generate TypeScript from `openapi.json`** into `frontend/src/lib/api-types.ts` using
`openapi-typescript` — a dev-only dependency with no runtime cost. The generated file is
**committed** so its diffs are reviewable.

**4. Fail CI on contract drift:**

```
dump openapi.json → regenerate api-types.ts → git diff --exit-code → tsc --noEmit
```

A non-empty diff means the committed types are stale. A `tsc` failure means the frontend no
longer matches the contract. Either way the build breaks before the deploy does.

**5. Type `api.ts` against the generated types.** The module keeps its current shape — a
thin, centralized set of named functions over axios. This decision does **not** replace
axios and does **not** replace TanStack Query, both of which are working well.

**6. Remove business orchestration from React.** `scanAllCompetitors` (`api.ts:14-56`)
moves to the backend per ADR 0003. `api.ts` becomes purely transport.

**7. Reorganize the frontend by feature** (`features/competitors/`, `features/products/`,
…) — **after** typing, not before. Moving untyped files first makes the typing diff
unreadable. The UI is not being redesigned.

## Alternatives considered

**Hand-write TypeScript interfaces mirroring the Pydantic models.** This is what
`SalesTrends.tsx:36-53` already does, and it is the only page with types — hand-written,
unverified, and already able to drift silently. Rejected: it produces the appearance of
safety without the guarantee.

**Generate a full typed client (openapi-fetch, orval, Kubb) and delete `api.ts`.**
Rejected for now. `api.ts` is already a good centralization point and the call sites are
readable. Swapping the HTTP layer *and* introducing generation in one change makes the
diff hard to review and mixes two decisions. Revisit once types exist.

**Generate Python types from a TypeScript-first schema.** Rejected — the backend is the
producer and FastAPI already emits OpenAPI for free.

**Add a runtime validator (zod) at the API boundary.** Rejected as redundant for a
single-operator app talking to its own backend. Compile-time checking plus CI drift
detection covers the actual risk, which is developer error rather than a hostile payload.

**Enable eslint.** Deferred, not decided. It requires adding a dependency that has never
been installed, and it is orthogonal to contract safety. Phase 1 should decide explicitly
whether to install it or remove the broken script — leaving a `lint` script that cannot run
is the worst of both.

## Consequences

**Positive**

- A backend response change breaks CI instead of a page.
- The `any` that matters most — `api.ts` — is eliminated, which types most call sites
  transitively.
- Declaring response models makes the OpenAPI docs at `/docs` genuinely useful.
- The typecheck gate catches errors like the `replaceAll` one at commit time.

**Negative**

- One new dev dependency (`openapi-typescript`).
- The committed generated file adds diff noise on every contract change — deliberate, since
  that noise *is* the signal.
- Steps 2 and 4 require the app to be importable in CI to dump the schema. This works
  today (verified) but couples CI to import-time behaviour.
- Typing the search and compare endpoints is genuinely awkward: they return deeply nested
  ad-hoc structures assembled in Python. Modelling them honestly may expose that some of
  those shapes should be simplified — which is useful information, but is extra work.

**Neutral**

- TanStack Query, axios, and the existing page structure are all preserved.

## Dependencies

Step 1 is complete (Phase 0). Steps 2–5 are independent of the backend restructure and can
proceed in parallel with ADR 0003 work. Step 6 depends on ADR 0003.
