"""Stage: cut, reframe, burn in captions, set loudness and write the MP4 (final) or a small preview of it."""

from __future__ import annotations

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg
from app.adapters.media.ffprobe import MediaValidationError, probe_streams
from app.pipeline.stages.clip_common import read_json, render_params, spec_of
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult
from app.services import render_plan as rp

params = render_params
MEASURE_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def run(ctx: StageContext) -> StageResult:
    src = ctx.source
    kind = str(ctx.job_params.get("kind"))
    key = (src.proxy_key or src.original_key) if kind == "preview" else src.original_key
    ass_key = ctx.inputs.get("clip_subtitles")
    if not key or not ass_key:
        raise StageError("no_original", "The source file or the captions are missing.")
    out = ctx.workdir / "clip.mp4"
    with ctx.storage.local_path(key) as src_path, ctx.storage.local_path(ass_key) as ass_path:
        w = h = None
        if src.has_video:  # the real picture size of the file we read (a preview reads the smaller editing copy)
            try:
                v = next(s for s in probe_streams(src_path).get("streams", []) if s.get("codec_type") == "video")
                w, h = int(v["width"]), int(v["height"])
            except (StopIteration, KeyError, ValueError, MediaValidationError) as exc:
                raise StageError("not_media", "The source file could not be read.") from exc
        measured = None
        load_key = ctx.inputs.get("clip_loudness")
        if kind == "final" and load_key:
            with ctx.storage.local_path(load_key) as lp:
                doc = read_json(lp)
            if not doc.get("skipped") and all(k in doc for k in MEASURE_KEYS):
                measured = {k: float(doc[k]) for k in MEASURE_KEYS}
        spec = spec_of(ctx, ass_path=str(ass_path), src_w=w, src_h=h, measured=measured)
        before, after = rp.build_parts(spec)
        try:
            run_ffmpeg(
                input_path=src_path,
                input_args=before,
                output_args=after,
                output_path=out,
                duration_s=spec.duration_s,
                timeout_s=ctx.settings.ffmpeg_timeout_seconds,
                threads=ctx.settings.ffmpeg_threads,
                on_progress=lambda p: ctx.progress(p, "Rendering" if kind == "final" else "Rendering a preview"),
                cancelled=ctx.cancelled,
            )
        except Cancelled as exc:
            raise StageError("cancelled", "Cancelled") from exc
        except FfmpegError as exc:
            raise StageError(exc.code, exc.message) from exc
    ow, oh = spec.size
    return StageResult(
        outputs=[
            OutputFile(
                kind="clip_render" if kind == "final" else "clip_preview",
                path=out,
                mime="video/mp4",
                source_field=None,
                ext="mp4",
                width=ow,
                height=oh,
                duration_s=spec.duration_s,
            )
        ]
    )
