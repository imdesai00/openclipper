"""`python -m app.adapters.sources.selfcheck`: try one real download inside the ingest container.

Prints PASS, SKIPPED (the network or YouTube blocked it) or FAIL (something in OpenClipper is broken).
Exit code 1 only for FAIL, so a blocked network is never reported as a pass and never breaks a build.
The video is Blender Foundation's "Big Buck Bunny" (Creative Commons Attribution 3.0).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from app.adapters.media.ffprobe import MediaValidationError, probe
from app.adapters.sources.ytdlp import YtDlpError, download_video, version
from app.config import get_settings

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
# Failures caused by where this runs, not by OpenClipper.
ENVIRONMENT_CODES = {
    "youtube_bot_check", "youtube_forbidden", "youtube_rate_limited", "tls_certificate_error",
    "network_unreachable", "ytdlp_timeout",
}  # fmt: skip


def main() -> int:
    settings = get_settings().model_copy(update={"ytdlp_max_height": 360})
    print(f"yt-dlp {version()}")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            res = download_video(URL, Path(tmp), settings=settings, progress=lambda *_: None, cancelled=lambda: False)
        except YtDlpError as exc:
            status = "SKIPPED" if exc.code in ENVIRONMENT_CODES else "FAIL"
            print(f"{status} ({exc.code}): {exc.message}")
            return 0 if status == "SKIPPED" else 1
        try:
            info = probe(res.path)
        except MediaValidationError as exc:
            print(f"FAIL (downloaded file is not valid media): {exc.message}")
            return 1
        print(f"PASS: {res.title!r} {info.width}x{info.height} {info.duration_s:.0f}s {info.video_codec}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
