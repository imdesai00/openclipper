"""Tasks that run only in the worker-asr image."""

from __future__ import annotations

import json
from typing import Any

from redis import Redis

from app.adapters.asr.faster_whisper import FasterWhisperEngine
from app.config import get_settings
from app.ports.asr import ASRError
from app.tasks.asr_heartbeat import beat_once
from app.tasks.celery_app import celery_app

DL_KEY = "asr:dl:{}"


@celery_app.task(name="asr.download_model")
def download_model(name: str) -> None:
    """Download a speech model, publishing progress where the admin UI reads it."""
    s = get_settings()
    r = Redis.from_url(s.redis_url)

    def put(status: str, percent: float, done: bool = False, error: str | None = None) -> None:
        body: dict[str, Any] = {"status": status, "percent": round(percent * 100), "done": done, "error": error}
        r.set(DL_KEY.format(name), json.dumps(body), ex=3600)

    try:
        engine = FasterWhisperEngine(s)
        engine.ensure_model(name, lambda p, m: put(m or "Downloading", p))
        beat_once(
            r, engine
        )  # the UI reads "which models exist" from this record; do not make it wait for the next beat
        put(f"Speech model {name} is ready", 1.0, done=True)
    except ASRError as exc:
        put("Failed", 0.0, done=True, error=exc.message)
    finally:
        r.close()
