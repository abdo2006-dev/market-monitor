import asyncio
import logging
from celery import Celery
from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "market_monitor",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=600,
    task_time_limit=900,
)

celery_app.conf.beat_schedule = {
    "check-scan-schedule": {
        "task": "app.workers.tasks.check_and_schedule_scans",
        "schedule": 60.0,  # every minute
    },
    "send-daily-summary": {
        "task": "app.workers.tasks.send_daily_summary_task",
        "schedule": 86400.0,  # daily
    },
}
