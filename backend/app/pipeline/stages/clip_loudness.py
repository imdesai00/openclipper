"""Stage: first loudness pass. Measures the clip's audio as the render will hear it, so the second pass can be exact."""

from __future__ import annotations

import json

from app.adapters.media.ffmpeg import FfmpegError, capture_ffmpeg
from app.pipeline.stages.clip_common import render_params, spec_of
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult
from app.services import render_plan as rp

params = render_params


def run(ctx: StageContext) -> StageResult:
    src = ctx.source
    out = ctx.workdir / "loudness.json"
    spec = spec_of(ctx, ass_path="unused", src_w=None, src_h=None, measured=None)
    if not src.has_audio or spec.mute or not src.original_key:
        out.write_text(json.dumps({"skipped": True}))
    else:
        ctx.progress(0.1, "Measuring loudness")
        with ctx.storage.local_path(src.original_key) as path:
            try:
                err = capture_ffmpeg(rp.measure_args(spec, str(path)), ctx.settings.ffmpeg_timeout_seconds)
                measured = rp.parse_loudnorm(err)
            except FfmpegError as exc:
                raise StageError(exc.code, exc.message) from exc
            except ValueError as exc:
                raise StageError("loudness_failed", "The loudness of this clip could not be measured.") from exc
        out.write_text(json.dumps({"skipped": False, **measured}))
    return StageResult(
        outputs=[OutputFile(kind="clip_analysis", path=out, mime="application/json", source_field=None, ext="json")]
    )
