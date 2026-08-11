# Architecture Decision Records

Each ADR records one decision, why it was made, what was rejected, and what it costs.
An ADR is a historical record: once Accepted, it is superseded rather than edited.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith with background workers | Accepted |
| [0002](0002-postgres-source-of-truth.md) | PostgreSQL is the source of truth; Alembic solely owns the schema | Accepted |
| [0003](0003-single-scan-pathway.md) | One authoritative scan execution pathway | Accepted |
| [0004](0004-scraper-adapters.md) | Scraper adapter architecture with per-platform contract tests | Accepted |
| [0005](0005-generated-api-contracts.md) | Generated and CI-verified frontend API contracts | Accepted |
| [0006](0006-background-jobs-and-delivery.md) | Background job execution and reliable notification delivery | **Partially accepted** — delivery mechanism Accepted; topology continued in ADR 0008 |
| [0007](0007-product-identity-and-reconciliation-lock.md) | Product identity invariants and serialized reconciliation | Accepted |
| [0008](0008-sync-execution-topology.md) | Sync execution topology | **Accepted** — initial GitHub Actions; Railway reliability upgrade |

ADRs 0001–0006 were written during Phase 0 (2026-08-10) and are supported by the audit in
`docs/ARCHITECTURE.md` Part A. ADR 0007 records the implemented Phase 1B.1 decision. See
`docs/PROJECT_STATUS.md` for implementation status of the earlier target decisions.

ADR 0008 was accepted and implemented in Phase 1B.2. Production database migration and
provider proof remain explicit owner-operated steps.

## Template

```markdown
# ADR NNNN — Title

**Status:** Proposed | Accepted | Superseded by ADR-NNNN | Rejected

## Context
What is true today that forces a decision. Cite files and line numbers. State evidence,
not impressions.

## Decision
What we will do, specifically enough to implement.

## Alternatives considered
Each realistic option and why it was not chosen. An ADR with no rejected alternatives is
not recording a decision.

## Consequences
Positive, negative, and neutral. Be honest about the negative ones — that is the part
future readers need.
```

## Rules

- One decision per ADR.
- Number sequentially; never renumber.
- Do not rewrite an Accepted ADR to reflect a change of mind. Write a new one and set the
  old one to `Superseded by ADR-NNNN`.
- Do not mark something Accepted that has not actually been decided. `Proposed` and
  `Open` are legitimate states, and an honestly Open decision is more useful than a
  fabricated conclusion.
