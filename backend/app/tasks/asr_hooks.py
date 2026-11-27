"""Loaded only inside worker-asr (ASR_WORKER=1): tells the app the transcription service is alive and what it holds."""

from __future__ import annotations

import logging
import threading

from celery.signals import worker_ready, worker_shutdown
from redis import Redis

from app.adapters.asr.faster_whisper import FasterWhisperEngine
from app.config import get_settings
from app.tasks.asr_heartbeat import beat_once

log = logging.getLogger(__name__)
_stop = threading.Event()


def _beat() -> None:
    s = get_settings()
    r = Redis.from_url(s.redis_url)
    engine = FasterWhisperEngine(s)
    while not _stop.is_set():
        try:
            beat_once(r, engine)
        except Exception:
            log.exception("asr heartbeat failed")
        _stop.wait(15)


@worker_ready.connect
def _start(**_: object) -> None:
    threading.Thread(target=_beat, daemon=True, name="asr-heartbeat").start()


@worker_shutdown.connect
def _stop_threads(**_: object) -> None:
    _stop.set()
