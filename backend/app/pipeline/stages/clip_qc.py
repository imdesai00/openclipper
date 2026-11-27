"""Stage: look at the finished file and say whether it is what was asked for. The report is stored with the render."""

from __future__ import annotations

from typing import Any

from app.adapters.media.ffmpeg import FfmpegError, capture_ffmpeg
from app.adapters.media.ffprobe import MediaValidationError, probe_streams
from app.pipeline.stages.clip_common import render_params, style_of, words_of
from app.pipeline.types import RenderDone, StageContext, StageError, StageResult
from app.services import caption_engine as ce
from app.services import clip_qc
from app.services import render_plan as rp

params = render_params


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run(ctx: StageContext) -> StageResult:
    jp = ctx.job_params
    key = ctx.inputs.get("clip_render")
    if not key:
        raise StageError("no_render", "There is no rendered clip to check.")
    kind = str(jp["kind"])
    start, end = int(jp["start_ms"]), int(jp["end_ms"])
    ow, oh = rp.output_size(kind, str(jp["aspect"]))
    audio = jp.get("audio") or {}
    silent_by_design = bool(audio.get("mute")) or not ctx.source.has_audio
    style = style_of(jp)
    cues = ce.build_cues(words_of(jp), start, end, style) if jp.get("captions_enabled", True) else []
    with ctx.storage.local_path(key) as path:
        ctx.progress(0.2, "Reading the file")
        try:
            info = probe_streams(path)
        except MediaValidationError as exc:
            raise StageError(exc.code, exc.message) from exc
        streams = info.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        black = silent = lufs = None
        if kind == "final":
            ctx.progress(0.5, "Measuring loudness, black frames and silence")
            # pic_th 0.85: captions cover some pixels; a frame that is black underneath them is still black
            vf = ["-vf", "blackdetect=d=0.2:pic_th=0.85:pix_th=0.10"] if v else ["-vn"]
            try:
                err = capture_ffmpeg(
                    ["-i", str(path), *vf, "-af", "ebur128=peak=true,silencedetect=n=-50dB:d=0.5", "-f", "null", "-"],
                    ctx.settings.ffmpeg_timeout_seconds,
                )
            except FfmpegError as exc:
                raise StageError(exc.code, exc.message) from exc
            black = rp.sum_black(err) if v else None
            silent = rp.sum_silence(err) if a else None
            lufs = rp.parse_integrated_lufs(err) if a else None
    m = clip_qc.Measurements(
        kind=kind,
        expected_w=ow,
        expected_h=oh,
        expected_duration_s=(end - start) / 1000,
        has_video_stream=v is not None,
        has_audio_stream=a is not None,
        width=int(v["width"]) if v else None,
        height=int(v["height"]) if v else None,
        video_duration_s=_f(v.get("duration")) if v else None,
        audio_duration_s=_f(a.get("duration")) if a else None,
        container_duration_s=_f((info.get("format") or {}).get("duration")),
        size_bytes=int((info.get("format") or {}).get("size") or 0),
        audio_expected_silent=silent_by_design,
        source_is_audio_only=not ctx.source.has_video,
        loudness_lufs=lufs,
        target_lufs=rp.TARGET_LUFS,
        black_s=black,
        silent_s=silent,
        caption_box=ce.caption_box(style, ow, oh, max((len(c.lines) for c in cues), default=1)) if cues else None,
        captions_expected=bool(cues),
        caption_drift_ms=ce.max_word_drift_ms(cues, words_of(jp), start) if cues else None,
    )
    report = clip_qc.evaluate(m)
    ctx.progress(1.0, "Checked")
    return StageResult(
        clip_render=RenderDone(
            render_id=str(jp["render_id"]),
            qc_status=str(report["status"]),
            qc_report=report,
            width=m.width or ow,
            height=m.height or oh,
            duration_ms=round((m.container_duration_s or (end - start) / 1000) * 1000),
            size_bytes=m.size_bytes,
            loudness_lufs=lufs if (lufs is not None and lufs > -70) else None,
        )
    )
