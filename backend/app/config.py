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
    # Catalog adapters stop when the provider proves there is no next page.
    # This is only a safety ceiling for unexpectedly large or looping sources:
    # 100 Shopify pages is at most 25,000 raw products per acquisition.
    DEFAULT_MAX_PAGES: int = 100
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
    # Single-operator application access gate. Production enables this and
    # supplies only a password hash plus an independent session-signing secret.
    APP_AUTH_ENABLED: bool = False
    APP_AUTH_PASSWORD_HASH: Optional[str] = None
    APP_AUTH_SESSION_SECRET: Optional[str] = None
    APP_AUTH_SESSION_TTL_SECONDS: int = 43200
    # Preview-only deterministic acquisition. The second VERCEL_ENV gate makes
    # this impossible to activate on a Vercel production deployment by setting
    # PREVIEW_DEMO_MODE alone.
    VERCEL_ENV: Optional[str] = None
    PREVIEW_DEMO_MODE: bool = False
    # Startup schema verification (docs/adr/0002). One of: strict | warn | off.
    # "warn" is the deliberate Phase 1A default so that removing the old
    # create_all-at-startup behaviour cannot take a running deployment down on
    # boot. Flip to "strict" once production has been verified and stamped.
    DB_SCHEMA_CHECK: str = "warn"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
