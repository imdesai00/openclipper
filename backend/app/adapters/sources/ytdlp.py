"""YouTube video links via the yt-dlp CLI (optional add-on, DECISIONS D-010).

Runs yt-dlp as a subprocess (argument list, timeout, cancel) so it can be updated without touching the app,
and so it never shares a process with anything that holds publishing credentials. It does not read the
YouTube OAuth tokens or API credentials, which are for the official API only (SPEC §3.1).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.adapters.sources.youtube_url import NotSupportedLinkError, normalize_video_url
from app.config import Settings

LIB_DIR_ENV = "YTDLP_LIB_DIR"  # holds a self-updated yt-dlp that takes precedence over the baked-in one


class YtDlpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class VideoResult:
    path: Path
    filename: str
    size: int
    ext: str
    title: str


# (substring in yt-dlp output, error code, message with a fix-it hint). Order matters: first match wins.
_ERRORS: list[tuple[str, str, str]] = [
    ("certificate verify failed", "tls_certificate_error",
     "The download service could not verify the HTTPS certificate of the site. A proxy, VPN or firewall on this "
     "network may be intercepting traffic. Add your organisation's CA certificate to the container, or use a "
     "network without interception."),
    ("name or service not known", "network_unreachable", "The download service has no internet access (DNS lookup failed)."),
    ("temporary failure in name resolution", "network_unreachable", "The download service has no internet access (DNS lookup failed)."),
    ("network is unreachable", "network_unreachable", "The download service has no internet access."),
    ("sign in to confirm", "youtube_bot_check",
     "YouTube asked to confirm you are not a bot. This usually happens on server or VPN IP addresses. "
     "Run OpenClipper on a home connection, enable the PO-token helper, or upload the file instead."),
    ("http error 403", "youtube_forbidden",
     "YouTube refused the download (403). The tool may be out of date or this IP is blocked. "
     "It will update itself; try again later, or upload the file instead."),
    ("http error 429", "youtube_rate_limited", "YouTube is rate-limiting this connection. Wait and try again later."),
    ("too many requests", "youtube_rate_limited", "YouTube is rate-limiting this connection. Wait and try again later."),
    ("private video", "video_private", "This video is private."),
    ("video unavailable", "video_unavailable", "This video is unavailable."),
    ("members-only", "video_members_only", "This video is for channel members only."),
    ("age", "youtube_age_restricted", "This video is age-restricted and needs a signed-in session, which is not supported."),
    ("drm", "drm_protected", "This video is DRM-protected. OpenClipper does not download DRM-protected content."),
    ("unsupported url", "unsupported_url", "That link is not supported."),
    ("requested format is not available", "format_unavailable",
     "No downloadable video format was found. A JavaScript runtime (Deno) is required by YouTube; "
     "check that the ingest service is healthy and up to date."),
    ("no supported javascript runtime", "js_runtime_missing", "The JavaScript runtime (Deno) needed by yt-dlp is missing."),
]  # fmt: skip
_RETRY_AFTER_UPDATE = {"youtube_forbidden", "format_unavailable", "js_runtime_missing", "ytdlp_failed"}


def _useful_lines(output: str) -> list[str]:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip() and ln.strip() != "null"]
    errors = [ln for ln in lines if ln.startswith("ERROR")]
    return errors or lines


def classify_error(output: str) -> YtDlpError:
    low = "\n".join(_useful_lines(output)).lower() or output.lower()
    for needle, code, msg in _ERRORS:
        if needle in low and (needle != "age" or "age-restricted" in low or "confirm your age" in low):
            return YtDlpError(code, msg)
    useful = _useful_lines(output)
    last = useful[-1] if useful else "yt-dlp gave no details (see the ingest worker log)"
    last = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*[\w-]+:\s*)?", "", last)  # drop the "ERROR: [youtube] id:" prefix
    return YtDlpError("ytdlp_failed", f"The download failed: {last[:300]}")


def _env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "LANG", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE"}
    }
    lib = os.environ.get(LIB_DIR_ENV)
    if lib and Path(lib).is_dir():
        env["PYTHONPATH"] = lib  # updated copy wins over the baked-in version
    return env


def _base_cmd(max_height: int) -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "--no-color", "--ignore-config",
           "--socket-timeout", "30", "--retries", "3"]  # fmt: skip
    if proxy := os.environ.get("YTDLP_PROXY"):
        cmd += ["--proxy", proxy]
    return cmd


def version() -> str | None:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_env(),
            check=False,
        )  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _run_capture(cmd: list[str], timeout_s: int) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=_env(), check=False)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        raise YtDlpError("ytdlp_timeout", "Looking up the video took too long") from exc
    except OSError as exc:
        raise YtDlpError("ytdlp_missing", "yt-dlp is not installed in this container") from exc
    if proc.returncode != 0:
        raise classify_error(proc.stderr or proc.stdout)  # errors are on stderr; stdout may hold unrelated JSON/"null"
    return proc.stdout


def fetch_metadata(url: str, settings: Settings) -> dict[str, object]:
    out = _run_capture(_base_cmd(settings.ytdlp_max_height) + ["--dump-single-json", "--skip-download", url], 180)
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise YtDlpError("ytdlp_failed", "Could not read the video information") from exc
    if not isinstance(data, dict):
        raise YtDlpError("ytdlp_failed", "Could not read the video information")
    return data


_PROGRESS = re.compile(r"^PROGRESS (\d+)/(\d+|NA)$")


def _download_once(url: str, workdir: Path, settings: Settings, progress: Callable[[float, str | None], None],
                   cancelled: Callable[[], bool]) -> VideoResult:  # fmt: skip
    h = settings.ytdlp_max_height
    fmt = f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/bv*[height<={h}]+ba/b[height<={h}]"
    cmd = _base_cmd(h) + [
        "-f", fmt, "--merge-output-format", "mp4", "--no-mtime", "--newline",
        "--progress-template", "download:PROGRESS %(progress.downloaded_bytes)s/%(progress.total_bytes)s",
        "-o", str(workdir / "download.%(ext)s"), url,
    ]  # fmt: skip
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_env(), stdin=subprocess.DEVNULL
    )  # noqa: S603
    tail: deque[str] = deque(maxlen=60)
    deadline = time.monotonic() + settings.ytdlp_timeout_seconds
    killed_for: str | None = None

    def watchdog() -> None:
        nonlocal killed_for
        while proc.poll() is None:
            if time.monotonic() > deadline:
                killed_for = "ytdlp_timeout"
            elif cancelled():
                killed_for = "cancelled"
            if killed_for:
                proc.kill()
                return
            time.sleep(1)

    t = threading.Thread(target=watchdog, daemon=True)
    t.start()
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        m = _PROGRESS.match(line.replace("download:", "").strip())
        if m and m.group(2) != "NA" and int(m.group(2)) > 0:
            progress(int(m.group(1)) / int(m.group(2)), "Downloading from YouTube")
    proc.wait()
    t.join(timeout=3)
    if killed_for == "cancelled":
        raise YtDlpError("cancelled", "Cancelled")
    if killed_for == "ytdlp_timeout":
        raise YtDlpError("ytdlp_timeout", "The download took too long and was stopped")
    if proc.returncode != 0:
        raise classify_error("\n".join(tail))
    files = sorted(p for p in workdir.glob("download.*") if p.is_file() and p.suffix not in {".part", ".ytdl"})
    if not files or files[0].stat().st_size == 0:
        raise YtDlpError("ytdlp_failed", "The download finished but no file was produced")
    f = files[0]
    return VideoResult(path=f, filename=f.name, size=f.stat().st_size, ext=f.suffix.lstrip("."), title="")


def download_video(url: str, workdir: Path, *, settings: Settings, progress: Callable[[float, str | None], None],
                   cancelled: Callable[[], bool]) -> VideoResult:  # fmt: skip
    try:
        url = normalize_video_url(url)
    except NotSupportedLinkError as exc:
        raise YtDlpError(exc.code, exc.message) from exc
    progress(0.0, "Looking up the video")
    try:
        meta = fetch_metadata(url, settings)
    except YtDlpError as exc:
        if exc.code not in _RETRY_AFTER_UPDATE:
            raise
        from app.adapters.sources.ytdlp_update import update_ytdlp

        update_ytdlp()  # extractor failures are usually fixed by a newer yt-dlp (SPEC §3.1)
        meta = fetch_metadata(url, settings)
    if meta.get("is_live") or meta.get("live_status") in ("is_live", "is_upcoming"):
        raise YtDlpError("live_not_supported", "Live streams are not supported yet. Use the recording after the stream ends, or upload a file.")  # fmt: skip
    formats = meta.get("formats")
    if isinstance(formats, list) and formats and all(isinstance(f, dict) and f.get("has_drm") for f in formats):
        raise YtDlpError("drm_protected", "This video is DRM-protected. OpenClipper does not download DRM-protected content.")  # fmt: skip
    res = _download_once(url, workdir, settings, progress, cancelled)
    res.title = str(meta.get("title") or url)
    return res
