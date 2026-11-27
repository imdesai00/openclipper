"""ffprobe wrapper + validation. Treats every input as hostile."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Container families we accept. ffprobe reports e.g. "mov,mp4,m4a,3gp,3g2,mj2" or "matroska,webm".
# Everything else (hls, concat, image2, rtsp, ...) is rejected, which also blocks the classic
# "playlist renamed to .mp4 makes ffmpeg fetch URLs / read local files" attack.
ALLOWED_FORMATS = {
    "mov", "mp4", "matroska", "webm", "avi", "mpegts", "flv", "asf", "mpeg",
    "mp3", "wav", "flac", "ogg", "aac",  # audio-only files (podcasts)
}  # fmt: skip
MAX_STREAMS = 64
MAX_PIXELS = 7680 * 4320
MIN_DURATION_S = 0.5
PROBE_TIMEOUT_S = 120


class MediaValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ProbeResult:
    container: str
    duration_s: float
    size_bytes: int
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    has_audio: bool
    has_video: bool
    fps: float | None
    bit_rate: int | None
    summary: dict[str, Any] = field(default_factory=dict)


def _fps(stream: dict[str, Any]) -> float | None:
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
    try:
        num, den = raw.split("/")
        return round(float(num) / float(den), 3) if float(den) else None
    except (ValueError, ZeroDivisionError):
        return None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def probe(path: Path) -> ProbeResult:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        str(path),
    ]
    try:
        proc = subprocess.run(  # noqa: S603  (argument list, no shell)
            cmd, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaValidationError("probe_timeout", "Reading the file took too long") from exc
    except FileNotFoundError as exc:
        raise MediaValidationError("ffprobe_missing", "ffprobe is not installed") from exc
    if proc.returncode != 0:
        raise MediaValidationError("not_media", "This file is not a readable video")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise MediaValidationError("not_media", "This file is not a readable video") from exc

    fmt = data.get("format") or {}
    names = {n.strip() for n in str(fmt.get("format_name", "")).split(",") if n.strip()}
    if not names & ALLOWED_FORMATS:
        raise MediaValidationError(
            "unsupported_container", f"Unsupported container: {fmt.get('format_name') or 'unknown'}"
        )
    streams = data.get("streams") or []
    if len(streams) > MAX_STREAMS:
        raise MediaValidationError("too_many_streams", "The file has too many streams")

    video = next(
        (
            s for s in streams
            if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )  # fmt: skip
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaValidationError("no_media_stream", "The file has no video or audio track")
    w = h = 0
    if video is not None:
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        if w <= 0 or h <= 0:
            raise MediaValidationError("bad_dimensions", "The video has invalid dimensions")
        if w * h > MAX_PIXELS:
            raise MediaValidationError("too_large_dimensions", "The video resolution is too large")

    duration = _num(fmt.get("duration")) or _num((video or audio or {}).get("duration"))
    if duration is None:
        raise MediaValidationError("no_duration", "Could not determine the video length")
    if duration < MIN_DURATION_S:
        raise MediaValidationError("too_short", "The video is too short")

    return ProbeResult(
        container=str(fmt.get("format_name", "")),
        duration_s=duration,
        size_bytes=int(_num(fmt.get("size")) or path.stat().st_size),
        width=w,
        height=h,
        video_codec=str(video.get("codec_name", "unknown")) if video else "",
        audio_codec=str(audio.get("codec_name")) if audio else None,
        has_audio=audio is not None,
        has_video=video is not None,
        fps=_fps(video) if video else None,
        bit_rate=int(b) if (b := _num(fmt.get("bit_rate"))) else None,
        summary={
            "format": {k: fmt.get(k) for k in ("format_name", "format_long_name", "duration", "size", "bit_rate")},
            "streams": [
                {
                    k: s.get(k)
                    for k in ("index", "codec_type", "codec_name", "width", "height", "channels", "sample_rate")
                }
                for s in streams
            ],
        },
    )


def probe_streams(path: Path, timeout_s: int = 60) -> dict[str, Any]:
    """Raw ffprobe output (streams and format) for a file WE produced. The caller decides what it means."""
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    try:
        proc = subprocess.run(  # noqa: S603  (argument list, no shell)
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaValidationError("probe_timeout", "Reading the file took too long") from exc
    if proc.returncode != 0:
        raise MediaValidationError("not_media", "The rendered file could not be read")
    data = json.loads(proc.stdout or "{}")
    return dict(data)
