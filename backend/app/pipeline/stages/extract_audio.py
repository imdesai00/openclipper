"""Stage: decode the original's first audio track to 16 kHz mono 16-bit WAV, the format speech recognition wants."""

from __future__ import annotations

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult


def params(_settings: object, job_params: dict[str, object] | None = None) -> dict[str, object]:
    return {"v": 1, "sr": 16000, "ch": 1}


def run(ctx: StageContext) -> StageResult:
    src = ctx.source
    if not src.original_key or not src.duration_s:
        raise StageError("no_original", "The original file is missing")
    if src.has_audio is False:
        raise StageError("no_audio", "This file has no sound to transcribe.")
    out = ctx.workdir / "audio.wav"
    with ctx.storage.local_path(src.original_key) as path:
        try:
            run_ffmpeg(
                input_path=path,
                output_path=out,
                duration_s=src.duration_s,
                output_args=["-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"],
                timeout_s=ctx.settings.ffmpeg_timeout_seconds,
                threads=ctx.settings.ffmpeg_threads,
                on_progress=lambda p: ctx.progress(p, "Extracting audio"),
                cancelled=ctx.cancelled,
            )
        except Cancelled as exc:
            raise StageError("cancelled", "Cancelled") from exc
        except FfmpegError as exc:
            raise StageError(exc.code, f"{exc.message}: {exc.stderr_tail[-300:]}".strip(": ")) from exc
    return StageResult(
        outputs=[
            OutputFile(
                kind="audio",
                path=out,
                mime="audio/wav",
                source_field="audio_asset_id",
                ext="wav",
                duration_s=src.duration_s,
            )
        ]
    )
