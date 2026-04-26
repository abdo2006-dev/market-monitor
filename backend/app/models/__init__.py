from sqlalchemy import (
    Column, Integer, String, Boolean, Numeric, JSON,
    DateTime, ForeignKey, Text, Float
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
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


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(255), nullable=True)
    title = Column(String(500), nullable=False)
    normalized_title = Column(String(500), nullable=False, index=True)
    url = Column(String(1000), nullable=False, index=True)
    image_url = Column(String(1000), nullable=True)
    current_price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(10), default="USD", nullable=False)
    stock_status = Column(String(50), default="unknown", nullable=False)
    sku = Column(String(255), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_checked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
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
    price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(10), default="USD", nullable=False)
    stock_status = Column(String(50), default="unknown", nullable=False)
    image_url = Column(String(1000), nullable=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

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

    competitor = relationship("Competitor", back_populates="events")
    product = relationship("Product", back_populates="events")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="running", nullable=False)
    products_found = Column(Integer, default=0, nullable=False)
    new_products_count = Column(Integer, default=0, nullable=False)
    price_changes_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    competitor = relationship("Competitor", back_populates="scrape_runs")


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
