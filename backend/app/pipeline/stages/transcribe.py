"""Stage: speech recognition. Runs only in the worker-asr image (queue `asr`)."""

from __future__ import annotations

from app.adapters.asr.faster_whisper import FasterWhisperEngine
from app.config import Settings
from app.pipeline.types import StageContext, StageError, StageResult
from app.ports.asr import ASRError


def params(s: Settings, job_params: dict[str, object] | None = None) -> dict[str, object]:
    jp = job_params or {}
    # Everything that changes the words must be here, so the same inputs are reused and different ones are not.
    return {
        "v": 1,
        "model": jp.get("model") or s.whisper_model,
        "language": jp.get("language"),
        "vad": jp.get("vad", s.asr_vad),
        "force": jp.get("force_nonce"),
    }


def run(ctx: StageContext) -> StageResult:
    s = ctx.settings
    key = ctx.inputs.get("asr_prep") or ctx.inputs.get("extract_audio")
    if not key:
        raise StageError("no_audio", "The extracted audio is missing.")
    p = params(s, ctx.job_params)
    engine = FasterWhisperEngine(s)
    try:
        engine.ensure_model(str(p["model"]), ctx.progress)
        with ctx.storage.local_path(key) as path:
            data = engine.transcribe(
                path,
                model=str(p["model"]),
                language=p["language"] if isinstance(p["language"], str) else None,
                vad=bool(p["vad"]),
                progress=ctx.progress,
                cancelled=ctx.cancelled,
            )
    except ASRError as exc:
        raise StageError(exc.code, exc.message) from exc
    if not data.segments:
        raise StageError("no_speech", "No speech was found in this recording.")
    return StageResult(transcript=data)
