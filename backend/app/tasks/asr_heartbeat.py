"""The speech worker's liveness record. Kept free of Celery signal hooks so tasks and tests can import it safely."""

from __future__ import annotations

import json

from redis import Redis

from app.adapters.asr.faster_whisper import FasterWhisperEngine
from app.services.transcripts import ASR_HEARTBEAT


def beat_once(r: Redis, engine: FasterWhisperEngine) -> None:
    """Publish liveness plus what this worker can do; the key expires, so a dead worker disappears by itself."""
    device, compute = engine.device_and_compute()
    body = {"device": device, "compute": compute, "downloaded": engine.downloaded_models()}
    r.set(ASR_HEARTBEAT, json.dumps(body), ex=60)
