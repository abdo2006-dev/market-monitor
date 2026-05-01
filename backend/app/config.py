from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://market:market@localhost:5432/market_monitor"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production"
    DEFAULT_TIMEZONE: str = "UTC"
    DEFAULT_CURRENCY: str = "USD"
    DISCORD_NOTIFICATIONS_ENABLED: bool = True
    DISCORD_DEFAULT_WEBHOOK_URL: Optional[str] = None
    USER_AGENT: str = "MarketMonitor/1.0 (price monitoring bot)"
    PLAYWRIGHT_HEADLESS: bool = True
    DEFAULT_SCAN_INTERVAL_MINUTES: int = 60
    DEFAULT_MAX_PAGES: int = 5
    DEFAULT_PAGE_DELAY_SECONDS: float = 2.0
    DAILY_SUMMARY_ENABLED: bool = True
    DAILY_SUMMARY_TIME: str = "08:00"
    MIN_PRICE_CHANGE_AMOUNT: float = 0.01
    MIN_PRICE_CHANGE_PERCENTAGE: float = 0.1
    IGNORE_KEYWORDS: str = ""
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    RUN_SCANS_INLINE: bool = False
    CRON_SECRET: Optional[str] = None

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
