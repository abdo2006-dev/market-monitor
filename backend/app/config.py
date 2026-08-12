from pydantic_settings import BaseSettings
from typing import Literal, Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://market:market@localhost:5432/market_monitor"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production"
    DEFAULT_TIMEZONE: str = "UTC"
    DEFAULT_CURRENCY: str = "USD"
    DISCORD_NOTIFICATIONS_ENABLED: bool = True
    DISCORD_DEFAULT_WEBHOOK_URL: Optional[str] = None
    USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
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
    # Explicit coexistence boundary. V2 uses PostgreSQL durable jobs; "legacy"
    # retains the pre-Phase-1B.2 Celery/inline pathways for production rollback.
    SYNC_EXECUTION_MODE: Literal["v2", "legacy"] = "v2"
    SYNC_MAX_ATTEMPTS: int = 3
    SYNC_LEASE_SECONDS: int = 600
    # Default-off rollout gate for every automatic morning request producer.
    # Manual V2 requests and explicit request-id workers remain available.
    SYNC_MORNING_ENABLED: bool = False
    SYNC_DISPATCH_PROVIDER: Literal["none", "github_actions"] = "none"
    GITHUB_ACTIONS_DISPATCH_TOKEN: Optional[str] = None
    GITHUB_ACTIONS_REPOSITORY: Optional[str] = None
    GITHUB_ACTIONS_WORKFLOW: str = "sync-v2.yml"
    GITHUB_ACTIONS_REF: str = "main"
    CRON_SECRET: Optional[str] = None
    # Startup schema verification (docs/adr/0002). One of: strict | warn | off.
    # "warn" is the deliberate Phase 1A default so that removing the old
    # create_all-at-startup behaviour cannot take a running deployment down on
    # boot. Flip to "strict" once production has been verified and stamped.
    DB_SCHEMA_CHECK: str = "warn"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
