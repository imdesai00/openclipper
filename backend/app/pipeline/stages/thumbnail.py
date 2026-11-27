"""Stage: poster frame for lists and the player."""

from __future__ import annotations

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult


def params(_settings: object, job_params: dict[str, object] | None = None) -> dict[str, object]:
    return {"v": 1, "width": 640, "at": 0.1}


def run(ctx: StageContext) -> StageResult:
    src = ctx.source
    if not src.original_key or not src.duration_s:
        raise StageError("no_original", "The original file is missing")
    if not src.has_video:
        ctx.progress(1.0, "No picture to extract")
        return StageResult()  # audio-only: nothing to do, and that is not an error
    out = ctx.workdir / "thumb.jpg"
    seek = max(0.0, min(src.duration_s * 0.1, src.duration_s - 0.1))
    with ctx.storage.local_path(src.original_key) as path:
        try:
            run_ffmpeg(
                input_path=path,
                input_args=["-ss", f"{seek:.3f}"],  # input-side seek: fast even on 2h files
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
        outputs=[
            OutputFile(kind="thumbnail", path=out, mime="image/jpeg", source_field="thumbnail_asset_id", ext="jpg")
        ]
    )
