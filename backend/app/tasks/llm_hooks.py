"""Loaded only inside worker-llm (LLM_WORKER=1): tells the API the highlight worker is alive."""

from __future__ import annotations

import logging
import threading

from celery.signals import worker_ready, worker_shutdown
from redis import Redis

from app.config import get_settings
from app.services.highlights import LLM_HEARTBEAT

log = logging.getLogger(__name__)
_stop = threading.Event()


def _beat() -> None:
    r = Redis.from_url(get_settings().redis_url)
    while not _stop.is_set():
        try:
            r.set(LLM_HEARTBEAT, "1", ex=60)  # expires by itself when the worker dies
        except Exception:
            log.exception("llm heartbeat failed")
        _stop.wait(15)


@worker_ready.connect
def _start(**_: object) -> None:
    threading.Thread(target=_beat, daemon=True, name="llm-heartbeat").start()


@worker_shutdown.connect
def _stop_threads(**_: object) -> None:
    _stop.set()
