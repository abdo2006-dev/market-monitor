# ADR 0001 — Modular monolith with background workers

**Status:** Accepted (Phase 0, 2026-08-10)

## Context

Market Monitor is a single-operator competitor price monitoring application. At baseline
`f346f70` it is ~7,400 lines of application source: a FastAPI backend, a Celery worker, a
React frontend, one PostgreSQL database, and one Redis instance.

The Phase 0 audit found that the problems are **boundary** problems, not scaling problems:

- The same scan operation is reachable through five divergent code paths (ARCH A-2).
- API route modules import private worker internals (ARCH A-4).
- One 1,195-line module implements three scraping platforms with no interface (ARCH A-8).
- Worker tasks mix transaction management, business rules, and webhook delivery (ARCH A-9).

Nothing in the audit indicated a scaling limit. Traffic is one operator. The database
holds thousands of products, not millions. The bottleneck is outbound scraping against
third-party sites, which is I/O-bound and rate-limited by politeness, not by our compute.

## Decision

Restructure as a **modular monolith with background workers**: one deployable application,
one database, explicit internal layer boundaries enforced by the dependency rule
(`api` and `workers` → `application` → `domain` + `ports`, with `infrastructure`
implementing `ports`).

Layers as defined in `docs/ARCHITECTURE.md` Part B.

## Alternatives considered

**Microservices** (scraper service, API service, notification service). Rejected. It would
convert in-process function calls into network calls that can fail independently, and
would require distributed transactions to preserve the one invariant that matters most —
that product changes, events, and outbox rows commit atomically. It multiplies deployment
and observability cost for an application with one user. None of the audit findings are
addressed by process separation; all of them are addressed by module separation.

**Leave the current structure and fix bugs individually.** Rejected. The defects are
consequences of the structure: A-5 (unscoped notifications) is unfixable without a
`ScrapeRun`↔`Event` relationship; A-6 (duplicate scans) is unfixable while five entry
points each implement their own guard. Patching each symptom leaves the mechanism intact.

**Full hexagonal architecture with strict DI everywhere.** Rejected as overengineering for
this size. We adopt the useful part — ports for external systems and a pure domain core —
without a DI container, without repository abstractions over trivial queries, and without
mapping layers between ORM models and domain entities where the ORM model is adequate.

**Serverless functions per operation.** Rejected. The current Vercel deployment already
demonstrates the cost: no worker means queued scans silently never run (ARCH A-11).
Long-running scrapes fit a worker model far better than a 300-second request budget.

## Consequences

**Positive**

- One authoritative implementation per behaviour; a policy change happens in one place.
- The domain becomes unit-testable without a database, a network, or Celery — currently
  impossible for `detect_changes`.
- Adapters give scrapers and notifiers a test seam and a contract.
- Deployment stays a single artifact; local development stays `docker compose up`.

**Negative**

- More files and more indirection than today. For a codebase this small that is a real
  cost, paid for by the boundaries.
- The layering must be enforced by review; nothing in Python prevents an import that
  violates the dependency rule. A lint rule for import direction is a Phase 1 candidate.
- The refactor touches the scan path, which is the highest-risk code in the repository.
  It must be done incrementally behind the existing signatures, with fixtures captured
  from current behaviour first.

**Neutral**

- Neither Celery nor Redis is replaced. They become infrastructure behind a `Queue` port.
- The frontend is unaffected by this decision; see ADR 0005.

## Explicitly excluded

Kubernetes, Kafka, service mesh, event sourcing as a general storage pattern, CQRS read
models, enterprise IAM. If any becomes warranted, it needs its own ADR with a concrete
triggering condition — not an assumption of future scale.
