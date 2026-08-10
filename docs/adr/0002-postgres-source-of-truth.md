# ADR 0002 — PostgreSQL is the source of truth; Alembic solely owns the schema

**Status:** Accepted (Phase 0, 2026-08-10)

## Context

The schema is currently defined **twice**, and the two definitions disagree.

`backend/app/database.py:53` runs on every FastAPI startup:

```python
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE product_snapshots ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
```

Alembic separately owns the same schema through `0001_initial` and
`0002_product_category`.

Verified experimentally during the Phase 0 audit against PostgreSQL 16, building one
database each way:

| | Alembic-built | `create_all`-built |
|---|---|---|
| `alembic_version` table | present | **absent** |
| `product_snapshots` indexes | `ix_snapshots_checked_at`, `ix_snapshots_product_id` | `ix_product_snapshots_checked_at`, `ix_product_snapshots_id`, `ix_product_snapshots_product_id` |

Consequences already live: a `create_all`-built database cannot be migrated (the next
`alembic upgrade head` runs `0001_initial` against populated tables and fails), and index
names differ by creation path. `docker-compose.yml:57` runs migrations before uvicorn so
Compose is accidentally safe; Vercel runs no migration step, so the deployed database is
almost certainly `create_all`-shaped and unstamped.

Redis is present as a Celery broker and result backend. Nothing else stores state.

## Decision

1. **PostgreSQL is the durable source of truth.** Redis holds queue messages and
   transient results only. No application state may live solely in Redis; anything that
   must survive a Redis flush belongs in PostgreSQL.
2. **Alembic is the only writer of schema.** `create_all` and ad-hoc DDL are removed from
   application startup. Migrations run as an explicit deployment step.
3. Every schema change ships as a migration with a real `downgrade()`.
4. CI verifies that `alembic upgrade head` succeeds on a clean database and that
   autogenerate against a migrated database produces an empty diff.

## Alternatives considered

**Keep `create_all` and drop Alembic.** Rejected. `create_all` cannot alter or drop
anything, so every column change becomes a manual production intervention. It also
provides no history and no rollback.

**Keep both, with `create_all` as a convenience for fresh local databases.** Rejected —
this is exactly the current state, and it is what produced the drift. The convenience is
small (`alembic upgrade head` is one command) and the failure mode is a production
database that cannot be migrated.

**Move to a schema-diff tool (Atlas, migra) instead of Alembic.** Rejected for Phase 0:
it introduces a new dependency and a new workflow to solve a problem caused by not using
the tool already present.

## Consequences

**Positive**

- One schema definition. Autogenerate becomes trustworthy.
- Migrations are reviewable and reversible; deploys become predictable.
- CI can assert that models and migrations agree — currently it cannot.

**Negative**

- **Fresh databases require an explicit migration step.** Any deployment that previously
  relied on startup DDL will start with no tables until migrations run. This is a real
  operational change and must be reflected in the Vercel deploy process, which currently
  has no migration step at all.
- **The existing production database must be reconciled before `init_db` is changed.**
  Removing the startup DDL from an unstamped database leaves it unmigratable. The
  recovery procedure — inspect, back up, `alembic stamp 0002_product_category`, verify —
  is documented in `docs/RUNBOOK.md` §2.2 and **must** be executed first.
- The index-name divergence persists in any existing database. A future migration that
  touches those indexes must tolerate both spellings.

**Neutral**

- Alembic already exists and works; this decision removes a competing mechanism rather
  than adding one.

## Follow-on work (not decided here)

Adding the missing constraints identified in ARCH A-7 — `UNIQUE (competitor_id, url)`, a
partial unique index on `external_id`, and check constraints on the free-text status
columns — requires a data-cleanup migration because duplicates may already exist. That is
Phase 1 work and needs its own dry-run report before the constraint is added.
