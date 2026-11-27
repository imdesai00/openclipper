"""Stage: a poster frame taken from the finished clip, so the thumbnail shows what was actually rendered."""

from __future__ import annotations

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg
from app.pipeline.stages.clip_common import render_params
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult

params = render_params


def run(ctx: StageContext) -> StageResult:
    key = ctx.inputs.get("clip_render")
    if not key:
        raise StageError("no_render", "There is no rendered clip to take a thumbnail from.")
    dur = (int(ctx.job_params["end_ms"]) - int(ctx.job_params["start_ms"])) / 1000
    at = max(0.0, min(dur * 0.2, dur - 0.2))
    out = ctx.workdir / "thumb.jpg"
    with ctx.storage.local_path(key) as path:
        try:
            run_ffmpeg(
                input_path=path,
                input_args=["-ss", f"{at:.3f}"],
                output_args=["-map", "0:v:0", "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4"],
                output_path=out,
                duration_s=None,
                timeout_s=300,
                threads=ctx.settings.ffmpeg_threads,
                cancelled=ctx.cancelled,
            )
        except Cancelled as exc:
            raise StageError("cancelled", "Cancelled") from exc
        except FfmpegError as exc:
            raise StageError(exc.code, exc.message) from exc
    return StageResult(
        outputs=[OutputFile(kind="thumbnail", path=out, mime="image/jpeg", source_field=None, ext="jpg")]
    )
