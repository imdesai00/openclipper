"""Quality checks on a rendered clip. Pure: the stage measures, this decides.

Each check has a severity. `fail` means the file should not be published as it is; `warn` means look at it; the overall
status is the worst one. Nothing here is a guess about taste: every check compares a measurement with what was asked."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["pass", "warn", "fail"]
_ORDER = {"pass": 0, "warn": 1, "fail": 2}

DURATION_TOL_S = 0.35
AV_SYNC_TOL_S = 0.15
LOUDNESS_TOL_LU = 2.0
BLACK_WARN_RATIO = 0.10
BLACK_FAIL_RATIO = 0.90
CAPTION_DRIFT_TOL_MS = 80


@dataclass
class Measurements:
    kind: str  # preview | final
    expected_w: int
    expected_h: int
    expected_duration_s: float
    has_video_stream: bool
    has_audio_stream: bool
    width: int | None
    height: int | None
    video_duration_s: float | None
    audio_duration_s: float | None
    container_duration_s: float | None
    size_bytes: int
    audio_expected_silent: bool  # muted, or the source itself has no sound
    source_is_audio_only: bool
    loudness_lufs: float | None = None  # measured on the finished file (final renders only)
    target_lufs: float = -14.0
    black_s: float | None = None
    silent_s: float | None = None
    caption_box: tuple[int, int, int, int] | None = None  # left, top, right, bottom in output pixels
    captions_expected: bool = False
    caption_drift_ms: int | None = None
    notes: list[str] = field(default_factory=list)


def _check(name: str, severity: Severity, message: str, **data: Any) -> dict[str, Any]:
    return {"check": name, "severity": severity, "message": message, **data}


def evaluate(m: Measurements) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    if not m.has_video_stream:
        checks.append(_check("video_stream", "fail", "The file has no picture."))
    else:
        checks.append(_check("video_stream", "pass", "Has a picture."))
    if m.has_audio_stream:
        checks.append(_check("audio_stream", "pass", "Has sound."))
    else:
        checks.append(_check("audio_stream", "fail", "The file has no audio track."))

    if m.width is not None and (m.width, m.height) != (m.expected_w, m.expected_h):
        checks.append(
            _check("dimensions", "fail", f"Size is {m.width}x{m.height}, expected {m.expected_w}x{m.expected_h}.")
        )
    elif m.width is not None:
        checks.append(_check("dimensions", "pass", f"{m.width}x{m.height}."))

    dur = m.container_duration_s if m.container_duration_s is not None else m.video_duration_s
    if dur is None:
        checks.append(_check("duration", "fail", "The length could not be read."))
    else:
        off = abs(dur - m.expected_duration_s)
        tol = max(DURATION_TOL_S, m.expected_duration_s * 0.02)
        sev: Severity = "pass" if off <= tol else "fail"
        checks.append(
            _check("duration", sev, f"{dur:.2f} s, asked for {m.expected_duration_s:.2f} s (off by {off:.2f} s).")
        )

    if m.video_duration_s is not None and m.audio_duration_s is not None:
        skew = abs(m.video_duration_s - m.audio_duration_s)
        sev = "pass" if skew <= AV_SYNC_TOL_S else "fail"
        checks.append(_check("av_sync", sev, f"Picture and sound differ in length by {skew:.2f} s."))

    if m.size_bytes <= 0:
        checks.append(_check("file", "fail", "The file is empty."))

    if m.black_s is not None and dur and not m.source_is_audio_only:
        ratio = m.black_s / dur
        if ratio >= BLACK_FAIL_RATIO:
            checks.append(_check("black_frames", "fail", f"{ratio:.0%} of the clip is black."))
        elif ratio >= BLACK_WARN_RATIO:
            checks.append(_check("black_frames", "warn", f"{ratio:.0%} of the clip is black."))
        else:
            checks.append(_check("black_frames", "pass", "No significant black frames."))

    if m.silent_s is not None and dur and m.has_audio_stream:
        ratio = m.silent_s / dur
        if m.audio_expected_silent:
            checks.append(_check("silence", "pass", "Silent on purpose."))
        elif ratio >= 0.98:
            checks.append(_check("silence", "fail", "The clip is silent, but the source has sound."))
        elif ratio >= 0.5:
            checks.append(_check("silence", "warn", f"{ratio:.0%} of the clip is silent."))
        else:
            checks.append(_check("silence", "pass", "Has sound throughout."))

    if m.kind == "final" and m.loudness_lufs is not None and not m.audio_expected_silent:
        off = abs(m.loudness_lufs - m.target_lufs)
        sev = "pass" if off <= LOUDNESS_TOL_LU else "warn"
        checks.append(
            _check("loudness", sev, f"{m.loudness_lufs:.1f} LUFS, target {m.target_lufs:.0f}.", lufs=m.loudness_lufs)
        )

    if m.captions_expected and m.caption_box is not None:
        left, top, right, bottom = m.caption_box
        inside = (
            left >= 0
            and top >= 0
            and right <= m.expected_w
            and bottom <= m.expected_h
            and right > left
            and bottom > top
        )
        checks.append(
            _check(
                "caption_bounds",
                "pass" if inside else "fail",
                "Captions sit inside the picture." if inside else "Captions would run outside the picture.",
            )
        )
    if m.captions_expected and m.caption_drift_ms is not None:
        ok = m.caption_drift_ms <= CAPTION_DRIFT_TOL_MS
        checks.append(
            _check(
                "caption_sync",
                "pass" if ok else "fail",
                f"Captions are within {m.caption_drift_ms} ms of the spoken words."
                if ok
                else f"Captions drift {m.caption_drift_ms} ms from the spoken words.",
            )
        )

    worst: Severity = "pass"
    for c in checks:
        if _ORDER[c["severity"]] > _ORDER[worst]:
            worst = c["severity"]
    return {"status": worst, "checks": checks, "notes": m.notes}
