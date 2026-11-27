"""Stage: level (and optionally denoise) the audio for RECOGNITION ONLY.

This copy is never used in an exported clip; final loudness is handled separately at render time (SPEC §6.C step 16).
"""

from __future__ import annotations

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg
from app.config import Settings
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult

LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"  # single pass, dynamic: good enough for recognition, fast on long files
DENOISE = "afftdn=nf=-25"


def filters(loudnorm: bool, denoise: bool) -> list[str]:
    return ([DENOISE] if denoise else []) + ([LOUDNORM] if loudnorm else [])


def options(s: Settings, job_params: dict[str, object] | None) -> tuple[bool, bool]:
    jp = job_params or {}
    return bool(jp.get("loudnorm", s.asr_loudnorm)), bool(jp.get("denoise", s.asr_denoise))


def params(s: Settings, job_params: dict[str, object] | None = None) -> dict[str, object]:
    ln, dn = options(s, job_params)
    return {"v": 1, "loudnorm": ln, "denoise": dn}


def run(ctx: StageContext) -> StageResult:
    ln, dn = options(ctx.settings, ctx.job_params)
    chain = filters(ln, dn)
    audio_key = ctx.inputs.get("extract_audio")
    if not audio_key:
        raise StageError("no_audio", "The extracted audio is missing.")
    if not chain:
        ctx.progress(1.0, "No preparation needed")
        return StageResult()  # the transcribe stage falls back to the plain extracted audio
    out = ctx.workdir / "audio_prep.wav"
    duration = ctx.source.duration_s
    with ctx.storage.local_path(audio_key) as path:
        try:
            run_ffmpeg(
                input_path=path,
                output_path=out,
                duration_s=duration,
                output_args=["-af", ",".join(chain), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"],
                timeout_s=ctx.settings.ffmpeg_timeout_seconds,
                threads=ctx.settings.ffmpeg_threads,
                on_progress=lambda p: ctx.progress(p, "Levelling audio for recognition"),
                cancelled=ctx.cancelled,
            )
        except Cancelled as exc:
            raise StageError("cancelled", "Cancelled") from exc
        except FfmpegError as exc:
            raise StageError(exc.code, f"{exc.message}: {exc.stderr_tail[-300:]}".strip(": ")) from exc
    return StageResult(
        outputs=[OutputFile(kind="audio_prep", path=out, mime="audio/wav", source_field=None, ext="wav")]
    )
