"""Loaded only inside worker-asr (ASR_WORKER=1): tells the app the transcription service is alive and what it holds."""

from __future__ import annotations

import json
import logging
import threading

from celery.signals import worker_ready, worker_shutdown
from redis import Redis

from app.adapters.asr.faster_whisper import FasterWhisperEngine
from app.config import get_settings
from app.services.transcripts import ASR_HEARTBEAT

log = logging.getLogger(__name__)
_stop = threading.Event()


def beat_once(r: Redis, engine: FasterWhisperEngine) -> None:
    """Publish liveness plus what this worker can do; the key expires, so a dead worker disappears by itself."""
    device, compute = engine.device_and_compute()
    body = {"device": device, "compute": compute, "downloaded": engine.downloaded_models()}
    r.set(ASR_HEARTBEAT, json.dumps(body), ex=60)


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
