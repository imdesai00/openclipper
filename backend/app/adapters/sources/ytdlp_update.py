"""Self-update of yt-dlp (+ its JS challenge solver) into a writable directory (SPEC §3.1)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

from app.adapters.sources.ytdlp import LIB_DIR_ENV, version

log = logging.getLogger(__name__)


def update_ytdlp() -> str | None:
    lib = os.environ.get(LIB_DIR_ENV)
    uv = shutil.which("uv")
    if not lib or not uv:
        log.warning("yt-dlp self-update unavailable (needs %s and uv)", LIB_DIR_ENV)
        return version()
    before = version()
    cmd = [
        uv,
        "pip",
        "install",
        "--python",
        sys.executable,
        "--target",
        lib,
        "--upgrade",
        "yt-dlp[default]",
        "yt-dlp-ejs",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)  # noqa: S603
        if r.returncode != 0:
            log.error("yt-dlp update failed: %s", r.stderr[-500:])
    except (OSError, subprocess.TimeoutExpired):
        log.exception("yt-dlp update could not run")
    after = version()
    log.info("yt-dlp version %s -> %s", before, after)
    return after
