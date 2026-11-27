from __future__ import annotations

from celery import Celery
from kombu.entity import Queue

from app.config import get_settings

QUEUES = ("default", "media", "asr", "llm", "publish", "ingest")

_settings = get_settings()
celery_app = Celery(
    "openclipper",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["app.tasks.system"],
)
celery_app.conf.update(
    task_queues=[Queue(q) for q in QUEUES],
    task_default_queue="default",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    beat_schedule={
        "purge-expired-sessions": {
            "task": "system.purge_expired_sessions",
            "schedule": 3600.0,
            "options": {"queue": "default"},
        },
    },
)
