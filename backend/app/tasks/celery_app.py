from __future__ import annotations

import os

from celery import Celery
from kombu.entity import Queue

from app.config import get_settings

QUEUES = ("default", "media", "asr", "llm", "publish", "ingest")

_settings = get_settings()
_INCLUDE = ["app.tasks.system", "app.tasks.pipeline"]
if os.environ.get("INGEST_WORKER") == "1":  # the optional yt-dlp worker adds heartbeat + self-update
    _INCLUDE.append("app.tasks.ingest_hooks")
if os.environ.get("ASR_WORKER") == "1":  # the speech worker adds its model-download task and a liveness heartbeat
    _INCLUDE += ["app.tasks.asr", "app.tasks.asr_hooks"]
if os.environ.get("LLM_WORKER") == "1":  # the highlight worker reports that it is alive so the API can refuse early
    _INCLUDE.append("app.tasks.llm_hooks")
celery_app = Celery("openclipper", broker=_settings.redis_url, backend=_settings.redis_url, include=_INCLUDE)

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
        "purge-expired-uploads": {
            "task": "system.purge_expired_uploads",
            "schedule": 3600.0,
            "options": {"queue": "default"},
        },
        "recover-stuck-jobs": {
            "task": "system.recover_stuck_jobs",
            "schedule": 60.0,
            "options": {"queue": "default"},
        },
    },
)
