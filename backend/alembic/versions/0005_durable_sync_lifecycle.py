"""Add the durable, observable Sync V2 lifecycle.

Revision ID: 0005_durable_sync_lifecycle
Revises: 0004_product_identity_integrity
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_durable_sync_lifecycle"
down_revision = "0004_product_identity_integrity"
branch_labels = None
depends_on = None


NON_TERMINAL = (
    "status IN ('queued', 'running', 'retry_wait') AND trigger <> 'legacy'"
)


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT competitor_id, array_agg(id ORDER BY id) AS run_ids "
            "FROM scrape_runs WHERE status = 'running' "
            "GROUP BY competitor_id HAVING count(*) > 1"
        )
    ).fetchall()
    if duplicates:
        detail = "; ".join(
            f"competitor_id={row.competitor_id} run_ids={list(row.run_ids)}"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot add durable non-terminal run uniqueness while overlapping legacy "
            f"running rows exist: {detail}. Confirm no worker owns them, resolve them "
            "explicitly, then retry the migration."
        )

    op.create_table(
        "sync_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "dispatch_status", sa.String(length=50), server_default="not_requested", nullable=False
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_error_category", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_sync_requests_idempotency_key"),
    )

    op.add_column(
        "scrape_runs",
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
    )
    op.add_column("scrape_runs", sa.Column("terminal_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("trigger", sa.String(length=50), nullable=True))
    op.add_column("scrape_runs", sa.Column("idempotency_key", sa.String(length=255)))
    op.add_column("scrape_runs", sa.Column("acquisition_started_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("acquisition_completed_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("reconciled_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("attempt_count", sa.Integer(), nullable=True))
    op.add_column("scrape_runs", sa.Column("max_attempts", sa.Integer(), nullable=True))
    op.add_column("scrape_runs", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("claim_token", postgresql.UUID(as_uuid=True)))
    op.add_column("scrape_runs", sa.Column("claimed_by", sa.String(length=255)))
    op.add_column("scrape_runs", sa.Column("pages_fetched", sa.Integer(), nullable=True))
    op.add_column("scrape_runs", sa.Column("request_count", sa.Integer(), nullable=True))
    op.add_column("scrape_runs", sa.Column("page_cap_reached", sa.Boolean(), nullable=True))
    op.add_column("scrape_runs", sa.Column("acquisition_strategy", sa.String(length=100)))
    op.add_column("scrape_runs", sa.Column("completeness", sa.String(length=50), nullable=True))
    op.add_column("scrape_runs", sa.Column("completeness_reason", sa.String(length=500)))
    op.add_column("scrape_runs", sa.Column("observation_started_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("observation_completed_at", sa.DateTime(timezone=True)))
    op.add_column("scrape_runs", sa.Column("failure_category", sa.String(length=100)))

    op.execute(
        "UPDATE scrape_runs SET queued_at = started_at, trigger = 'legacy', "
        "attempt_count = 1, max_attempts = 1, pages_fetched = 0, request_count = 0, "
        "page_cap_reached = false, "
        "completeness = CASE WHEN status = 'success' THEN 'unknown' "
        "WHEN status = 'failed' THEN 'failed' ELSE 'unknown' END, "
        "terminal_at = CASE WHEN status IN ('success', 'failed') THEN finished_at END"
    )
    for column in (
        "queued_at",
        "trigger",
        "attempt_count",
        "max_attempts",
        "pages_fetched",
        "request_count",
        "page_cap_reached",
        "completeness",
    ):
        op.alter_column("scrape_runs", column, nullable=False)
    op.alter_column("scrape_runs", "started_at", nullable=True)

    op.create_unique_constraint(
        "uq_scrape_runs_idempotency_key", "scrape_runs", ["idempotency_key"]
    )
    op.create_check_constraint(
        "ck_scrape_runs_status",
        "scrape_runs",
        "status IN ('queued', 'running', 'retry_wait', 'success', 'failed', 'abandoned', 'stale_skipped')",
    )
    op.create_check_constraint(
        "ck_scrape_runs_completeness",
        "scrape_runs",
        "completeness IN ('unknown', 'complete', 'partial', 'suspicious_empty', 'failed')",
    )
    op.create_check_constraint(
        "ck_scrape_runs_attempts",
        "scrape_runs",
        "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
    )
    op.create_index(
        "uq_scrape_runs_competitor_non_terminal",
        "scrape_runs",
        ["competitor_id"],
        unique=True,
        postgresql_where=sa.text(NON_TERMINAL),
    )
    op.create_index(
        "ix_scrape_runs_claimable",
        "scrape_runs",
        ["status", "next_attempt_at", "queued_at"],
    )

    op.create_table(
        "sync_request_runs",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scrape_run_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["sync_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scrape_run_id"], ["scrape_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("request_id", "scrape_run_id"),
    )

    op.add_column("events", sa.Column("scrape_run_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_events_scrape_run_id", "events", "scrape_runs", ["scrape_run_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_events_scrape_run_id", "events", ["scrape_run_id"])

    op.add_column("product_snapshots", sa.Column("observed_at", sa.DateTime(timezone=True)))
    op.add_column("product_snapshots", sa.Column("scrape_run_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_product_snapshots_scrape_run_id",
        "product_snapshots",
        "scrape_runs",
        ["scrape_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_product_snapshots_scrape_run_id", "product_snapshots", ["scrape_run_id"])

    op.add_column("products", sa.Column("last_observed_at", sa.DateTime(timezone=True)))
    op.add_column("products", sa.Column("last_observed_run_id", sa.Integer(), nullable=True))
    op.execute("UPDATE products SET last_observed_at = last_seen_at")
    op.create_foreign_key(
        "fk_products_last_observed_run_id",
        "products",
        "scrape_runs",
        ["last_observed_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_products_last_observed_run_id", "products", type_="foreignkey")
    op.drop_column("products", "last_observed_run_id")
    op.drop_column("products", "last_observed_at")

    op.drop_index("ix_product_snapshots_scrape_run_id", table_name="product_snapshots")
    op.drop_constraint("fk_product_snapshots_scrape_run_id", "product_snapshots", type_="foreignkey")
    op.drop_column("product_snapshots", "scrape_run_id")
    op.drop_column("product_snapshots", "observed_at")

    op.drop_index("ix_events_scrape_run_id", table_name="events")
    op.drop_constraint("fk_events_scrape_run_id", "events", type_="foreignkey")
    op.drop_column("events", "scrape_run_id")
    op.drop_table("sync_request_runs")

    op.drop_index("ix_scrape_runs_claimable", table_name="scrape_runs")
    op.drop_index("uq_scrape_runs_competitor_non_terminal", table_name="scrape_runs")
    op.drop_constraint("ck_scrape_runs_attempts", "scrape_runs", type_="check")
    op.drop_constraint("ck_scrape_runs_completeness", "scrape_runs", type_="check")
    op.drop_constraint("ck_scrape_runs_status", "scrape_runs", type_="check")
    op.drop_constraint("uq_scrape_runs_idempotency_key", "scrape_runs", type_="unique")
    # Revision 0004 requires started_at. A never-claimed queued V2 row has no
    # execution start, so its durable queue timestamp is the only truthful
    # fallback available when intentionally rolling the schema back.
    op.execute("UPDATE scrape_runs SET started_at = queued_at WHERE started_at IS NULL")
    op.alter_column("scrape_runs", "started_at", nullable=False)
    for column in (
        "failure_category", "observation_completed_at", "observation_started_at",
        "completeness_reason", "completeness", "acquisition_strategy", "page_cap_reached",
        "request_count", "pages_fetched", "claimed_by", "claim_token", "heartbeat_at",
        "lease_expires_at", "claimed_at", "next_attempt_at", "max_attempts", "attempt_count",
        "reconciled_at", "acquisition_completed_at", "acquisition_started_at",
        "idempotency_key", "trigger", "terminal_at", "queued_at",
    ):
        op.drop_column("scrape_runs", column)
    op.drop_table("sync_requests")
