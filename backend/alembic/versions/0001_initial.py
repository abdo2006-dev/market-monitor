"""initial

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competitors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("scan_frequency_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("scrape_type", sa.String(50), nullable=False, server_default="generic_selector"),
        sa.Column("listing_urls", JSON, nullable=False, server_default="[]"),
        sa.Column("selector_config", JSON, nullable=False, server_default="{}"),
        sa.Column("discord_webhook_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_status", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_competitors_id", "competitors", ["id"])

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("normalized_title", sa.String(500), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("image_url", sa.String(1000), nullable=True),
        sa.Column("current_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("stock_status", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("sku", sa.String(255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("consecutive_misses", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_id", "products", ["id"])
    op.create_index("ix_products_competitor_id", "products", ["competitor_id"])
    op.create_index("ix_products_url", "products", ["url"])
    op.create_index("ix_products_normalized_title", "products", ["normalized_title"])
    op.create_index("ix_products_active", "products", ["active"])

    op.create_table(
        "product_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("stock_status", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("image_url", sa.String(1000), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_snapshots_product_id", "product_snapshots", ["product_id"])
    op.create_index("ix_snapshots_checked_at", "product_snapshots", ["checked_at"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("old_value", JSON, nullable=True),
        sa.Column("new_value", JSON, nullable=True),
        sa.Column("event_message", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notification_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notification_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_id", "events", ["id"])
    op.create_index("ix_events_competitor_id", "events", ["competitor_id"])
    op.create_index("ix_events_product_id", "events", ["product_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_detected_at", "events", ["detected_at"])

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("products_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_products_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_changes_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scrape_runs_competitor_id", "scrape_runs", ["competitor_id"])

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("default_scan_interval_minutes", sa.Integer(), server_default="60"),
        sa.Column("default_max_pages", sa.Integer(), server_default="5"),
        sa.Column("default_page_delay_seconds", sa.Float(), server_default="2.0"),
        sa.Column("discord_notifications_enabled", sa.Boolean(), server_default="true"),
        sa.Column("daily_summary_enabled", sa.Boolean(), server_default="true"),
        sa.Column("daily_summary_time", sa.String(10), server_default="08:00"),
        sa.Column("min_price_change_amount", sa.Numeric(10, 2), server_default="0.01"),
        sa.Column("min_price_change_percentage", sa.Float(), server_default="0.1"),
        sa.Column("ignore_keywords", sa.Text(), server_default=""),
        sa.Column("user_agent", sa.String(500), server_default="MarketMonitor/1.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("scrape_runs")
    op.drop_table("events")
    op.drop_table("product_snapshots")
    op.drop_table("products")
    op.drop_table("competitors")
