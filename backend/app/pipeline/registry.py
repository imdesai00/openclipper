from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.pipeline.stages import asr_prep, extract_audio, proxy, thumbnail, validate, waveform
from app.pipeline.types import StageContext, StageResult


@dataclass(frozen=True)
class StageDef:
    name: str
    queue: str
    run: Callable[[StageContext], StageResult]
    params: Callable[[Settings, dict[str, Any] | None], dict[str, Any]]


def _download_run(ctx: StageContext) -> StageResult:
    from app.pipeline.stages import download  # imported lazily: needs optional tooling

    return download.run(ctx)


def _download_params(settings: Settings, job_params: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.pipeline.stages import download

    return download.params(settings, job_params)


def _transcribe_run(ctx: StageContext) -> StageResult:
    from app.pipeline.stages import transcribe  # imported lazily: the speech library exists only in worker-asr

    return transcribe.run(ctx)


def _transcribe_params(settings: Settings, job_params: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.pipeline.stages import transcribe

    return transcribe.params(settings, job_params)


STAGES: dict[str, StageDef] = {
    "download": StageDef("download", "media", _download_run, _download_params),
    "validate": StageDef("validate", "media", validate.run, validate.params),
    "proxy": StageDef("proxy", "media", proxy.run, proxy.params),
    "waveform": StageDef("waveform", "media", waveform.run, waveform.params),
    "thumbnail": StageDef("thumbnail", "media", thumbnail.run, thumbnail.params),
    "extract_audio": StageDef("extract_audio", "media", extract_audio.run, extract_audio.params),
    "asr_prep": StageDef("asr_prep", "media", asr_prep.run, asr_prep.params),
    "transcribe": StageDef("transcribe", "asr", _transcribe_run, _transcribe_params),
}

COMMON_STAGES = ["validate", "proxy", "waveform", "thumbnail"]
TRANSCRIBE_STAGES = ["extract_audio", "asr_prep", "transcribe"]

ANALYZE = "analyze_source"
TRANSCRIBE = "transcribe_source"


def stage_names_for_job(job_kind: str, source_kind: str) -> list[str]:
    if job_kind == TRANSCRIBE:
        return list(TRANSCRIBE_STAGES)
    return COMMON_STAGES if source_kind == "upload" else ["download", *COMMON_STAGES]


def queue_for(stage: str, source_kind: str) -> str:
    # yt-dlp downloads run only in the optional, isolated ingest-worker.
    if stage == "download" and source_kind == "ytdlp":
        return "ingest"
    return STAGES[stage].queue
