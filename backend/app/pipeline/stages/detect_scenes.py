"""Stage: find scene cuts (shot changes) with ffmpeg's scene score. Cheap, and shared by every highlight run."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg
from app.config import Settings
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult

MIN_GAP_S = 1.0  # cuts closer than this are one cut (a flash or a fade shows up as several)
MAX_CUTS = 5000
_PTS = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def params(s: Settings, job_params: dict[str, object] | None = None) -> dict[str, object]:
    return {"v": 1, "threshold": s.scene_threshold}


def parse_cuts(text: str) -> list[int]:
    """Cut times in ms from ffmpeg's `metadata=print` output. Close cuts merge, the first of them wins."""
    out: list[int] = []
    for m in _PTS.finditer(text):
        ms = round(float(m.group(1)) * 1000)
        if not out or ms - out[-1] >= MIN_GAP_S * 1000:
            out.append(ms)
        if len(out) >= MAX_CUTS:
            break
    return out


def run(ctx: StageContext) -> StageResult:
    src = ctx.source
    if not src.has_video:
        ctx.progress(1.0, "No picture, so no scenes")
        return StageResult()  # audio-only: not an error; scoring simply leaves this signal out
    key = src.proxy_key or src.original_key
    if not key or not src.duration_s:
        raise StageError("no_original", "The video file is missing")
    threshold = ctx.settings.scene_threshold
    meta = ctx.workdir / "scenes.txt"
    with ctx.storage.local_path(key) as path:
        try:
            run_ffmpeg(
                input_path=path,
                # the filter graph is built from numbers and a path we made ourselves, never from user text
                output_args=[
                    "-an",
                    "-vf",
                    f"select='gt(scene,{threshold})',metadata=print:file={_esc(meta)}",
                    "-f",
                    "null",
                    "-",
                ],
                output_path=None,
                duration_s=src.duration_s,
                timeout_s=ctx.settings.ffmpeg_timeout_seconds,
                threads=ctx.settings.ffmpeg_threads,
                on_progress=lambda p: ctx.progress(p, "Looking for scene changes"),
                cancelled=ctx.cancelled,
            )
        except Cancelled as exc:
            raise StageError("cancelled", "Cancelled") from exc
        except FfmpegError as exc:
            raise StageError(exc.code, exc.message) from exc
    cuts = parse_cuts(meta.read_text() if meta.exists() else "")
    out = ctx.workdir / "scenes.json"
    out.write_text(
        json.dumps({"v": 1, "threshold": threshold, "duration_ms": round(src.duration_s * 1000), "cuts_ms": cuts})
    )
    return StageResult(
        outputs=[
            OutputFile(kind="scenes", path=out, mime="application/json", source_field="scenes_asset_id", ext="json")
        ]
    )


def _esc(p: Path) -> str:
    """Escape a path for an ffmpeg filter argument (colon, backslash and quote are special there)."""
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
