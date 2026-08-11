"""reconcile index names between create_all and alembic

Phase 1A. Until now the schema had two authors: Alembic, and
`Base.metadata.create_all` called from application startup. They disagreed on
index names for `product_snapshots`, and Alembic was missing two `id` indexes
that the models declare.

    Alembic-built                 create_all-built (model convention)
    ix_snapshots_product_id       ix_product_snapshots_product_id
    ix_snapshots_checked_at       ix_product_snapshots_checked_at
    (none)                        ix_product_snapshots_id
    (none)                        ix_scrape_runs_id

This migration converges both shapes on the model convention, so that
`alembic revision --autogenerate` produces an empty diff and the CI drift check
can become a required gate.

Every statement is written to be safe against BOTH starting shapes, and against
a database where this has already been partially applied. Index renames and
creations are non-destructive: no table data is touched.

Revision ID: 0003_reconcile_index_names
Revises: 0002_product_category
"""
from alembic import op


revision = "0003_reconcile_index_names"
down_revision = "0002_product_category"
branch_labels = None
depends_on = None


# (old_alembic_name, model_name)
RENAMES = [
    ("ix_snapshots_product_id", "ix_product_snapshots_product_id"),
    ("ix_snapshots_checked_at", "ix_product_snapshots_checked_at"),
]

# (index_name, table, column) that the models declare but 0001 never created.
ADDITIONS = [
    ("ix_product_snapshots_id", "product_snapshots", "id"),
    ("ix_scrape_runs_id", "scrape_runs", "id"),
]


def upgrade() -> None:
    conn = op.get_bind()

    # Rename only where the old name exists AND the new name does not, so this
    # is a no-op on a create_all-built database that already uses model names.
    for old_name, new_name in RENAMES:
        conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_class WHERE relname = '{old_name}' AND relkind = 'i')
                   AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = '{new_name}' AND relkind = 'i')
                THEN
                    ALTER INDEX {old_name} RENAME TO {new_name};
                END IF;
            END $$;
            """
        )
        # If both existed (a hand-repaired database), the old one is redundant.
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {old_name};")

    for index_name, table, column in ADDITIONS:
        conn.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column});"
        )


def downgrade() -> None:
    conn = op.get_bind()

    for index_name, _table, _column in ADDITIONS:
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {index_name};")

    for old_name, new_name in RENAMES:
        conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_class WHERE relname = '{new_name}' AND relkind = 'i')
                   AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = '{old_name}' AND relkind = 'i')
                THEN
                    ALTER INDEX {new_name} RENAME TO {old_name};
                END IF;
            END $$;
            """
        )
