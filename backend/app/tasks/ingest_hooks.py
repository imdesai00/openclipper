"""Loaded only inside the optional ingest worker (INGEST_WORKER=1): liveness heartbeat + scheduled yt-dlp updates."""

from __future__ import annotations

import logging
import threading

from celery.signals import worker_ready, worker_shutdown
from redis import Redis

from app.adapters.sources.ytdlp import version
from app.adapters.sources.ytdlp_update import update_ytdlp
from app.config import get_settings
from app.services.ingest_settings import HEARTBEAT_KEY

log = logging.getLogger(__name__)
UPDATE_EVERY_S = 12 * 3600
_stop = threading.Event()


def _beat() -> None:
    r = Redis.from_url(get_settings().redis_url)
    while not _stop.is_set():
        try:
            r.set(HEARTBEAT_KEY, version() or "unknown", ex=60)
        except Exception:
            log.exception("ingest heartbeat failed")
        _stop.wait(20)


def _updater() -> None:
    update_ytdlp()  # at startup, then on a schedule
    while not _stop.wait(UPDATE_EVERY_S):
        update_ytdlp()


@worker_ready.connect
def _start(**_: object) -> None:
    threading.Thread(target=_beat, daemon=True, name="ingest-heartbeat").start()
    threading.Thread(target=_updater, daemon=True, name="ytdlp-updater").start()


@worker_shutdown.connect
def _stop_threads(**_: object) -> None:
    _stop.set()
