from sqlalchemy import (
    Column, Integer, String, Boolean, Numeric, JSON,
    DateTime, ForeignKey, Text, Float, Index, UniqueConstraint, text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.database import Base


class Competitor(Base):
    __tablename__ = "competitors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(500), nullable=False)
    category = Column(String(100), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    scan_frequency_minutes = Column(Integer, default=60, nullable=False)
    scrape_type = Column(String(50), default="generic_selector", nullable=False)
    listing_urls = Column(JSON, default=list, nullable=False)
    selector_config = Column(JSON, default=dict, nullable=False)
    discord_webhook_url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    last_scan_at = Column(DateTime(timezone=True), nullable=True)
    last_scan_status = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    products = relationship("Product", back_populates="competitor", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="competitor", cascade="all, delete-orphan")
    scrape_runs = relationship("ScrapeRun", back_populates="competitor", cascade="all, delete-orphan")


class SyncRequest(Base):
    """A durable user/scheduler intent that groups one or more competitor runs."""

    __tablename__ = "sync_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trigger = Column(String(50), nullable=False)
    idempotency_key = Column(String(255), nullable=True, unique=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    dispatch_status = Column(String(50), default="not_requested", nullable=False)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    dispatch_error_category = Column(String(100), nullable=True)

    runs = relationship(
        "ScrapeRun",
        secondary="sync_request_runs",
        back_populates="requests",
        order_by="ScrapeRun.id",
    )


class SyncRequestRun(Base):
    __tablename__ = "sync_request_runs"

    request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sync_requests.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scrape_run_id = Column(
        Integer,
        ForeignKey("scrape_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "competitor_id", "canonical_url",
            name="uq_products_competitor_canonical_url",
        ),
        Index(
            "uq_products_competitor_identity_key",
            "competitor_id", "identity_key",
            unique=True,
            postgresql_where=text("identity_key IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(255), nullable=True)
    title = Column(String(500), nullable=False)
    normalized_title = Column(String(500), nullable=False, index=True)
    category = Column(String(100), nullable=True, index=True)
    url = Column(String(1000), nullable=False, index=True)
    canonical_url = Column(String(1000), nullable=False)
    identity_key = Column(String(320), nullable=True)
    image_url = Column(String(1000), nullable=True)
    current_price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(10), default="USD", nullable=False)
    stock_status = Column(String(50), default="unknown", nullable=False)
    sku = Column(String(255), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_checked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_observed_at = Column(DateTime(timezone=True), nullable=True)
    last_observed_run_id = Column(
        Integer, ForeignKey("scrape_runs.id", ondelete="SET NULL"), nullable=True
    )
    active = Column(Boolean, default=True, nullable=False, index=True)
    consecutive_misses = Column(Integer, default=0, nullable=False)

    competitor = relationship("Competitor", back_populates="products")
    snapshots = relationship("ProductSnapshot", back_populates="product", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="product")


class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    category = Column(String(100), nullable=True)
    price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(10), default="USD", nullable=False)
    stock_status = Column(String(50), default="unknown", nullable=False)
    image_url = Column(String(1000), nullable=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    observed_at = Column(DateTime(timezone=True), nullable=True)
    scrape_run_id = Column(
        Integer, ForeignKey("scrape_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    product = relationship("Product", back_populates="snapshots")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    event_message = Column(Text, nullable=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    notification_sent = Column(Boolean, default=False, nullable=False)
    notification_sent_at = Column(DateTime(timezone=True), nullable=True)
    scrape_run_id = Column(
        Integer, ForeignKey("scrape_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    competitor = relationship("Competitor", back_populates="events")
    product = relationship("Product", back_populates="events")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    __table_args__ = (
        Index(
            "uq_scrape_runs_competitor_non_terminal",
            "competitor_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'running', 'retry_wait') AND trigger <> 'legacy'"
            ),
        ),
        Index("ix_scrape_runs_claimable", "status", "next_attempt_at", "queued_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False, index=True)
    queued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="queued", nullable=False)
    trigger = Column(String(50), default="legacy", nullable=False)
    idempotency_key = Column(String(255), nullable=True, unique=True)
    acquisition_started_at = Column(DateTime(timezone=True), nullable=True)
    acquisition_completed_at = Column(DateTime(timezone=True), nullable=True)
    reconciled_at = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    claim_token = Column(UUID(as_uuid=True), nullable=True)
    claimed_by = Column(String(255), nullable=True)
    products_found = Column(Integer, default=0, nullable=False)
    pages_fetched = Column(Integer, default=0, nullable=False)
    request_count = Column(Integer, default=0, nullable=False)
    page_cap_reached = Column(Boolean, default=False, nullable=False)
    acquisition_strategy = Column(String(100), nullable=True)
    completeness = Column(String(50), default="unknown", nullable=False)
    completeness_reason = Column(String(500), nullable=True)
    observation_started_at = Column(DateTime(timezone=True), nullable=True)
    observation_completed_at = Column(DateTime(timezone=True), nullable=True)
    failure_category = Column(String(100), nullable=True)
    new_products_count = Column(Integer, default=0, nullable=False)
    price_changes_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    competitor = relationship("Competitor", back_populates="scrape_runs")
    requests = relationship(
        "SyncRequest", secondary="sync_request_runs", back_populates="runs"
    )
    events = relationship("Event", foreign_keys="Event.scrape_run_id")
    snapshots = relationship("ProductSnapshot", foreign_keys="ProductSnapshot.scrape_run_id")


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, default=1)
    default_scan_interval_minutes = Column(Integer, default=60)
    default_max_pages = Column(Integer, default=5)
    default_page_delay_seconds = Column(Float, default=2.0)
    discord_notifications_enabled = Column(Boolean, default=True)
    daily_summary_enabled = Column(Boolean, default=True)
    daily_summary_time = Column(String(10), default="08:00")
    min_price_change_amount = Column(Numeric(10, 2), default=0.01)
    min_price_change_percentage = Column(Float, default=0.1)
    ignore_keywords = Column(Text, default="")
    user_agent = Column(String(500), default="MarketMonitor/1.0")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
