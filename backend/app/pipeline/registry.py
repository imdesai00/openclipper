from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.pipeline.stages import (
    asr_prep,
    clip_loudness,
    clip_qc,
    clip_render,
    clip_subtitles,
    clip_thumbnail,
    detect_scenes,
    extract_audio,
    proxy,
    thumbnail,
    validate,
    waveform,
)
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


def _highlights_run(ctx: StageContext) -> StageResult:
    from app.pipeline.stages import highlights  # lazy: pulls in the model service, which only the llm worker needs

    return highlights.run(ctx)


def _highlights_params(settings: Settings, job_params: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.pipeline.stages import highlights

    return highlights.params(settings, job_params)


STAGES: dict[str, StageDef] = {
    "download": StageDef("download", "media", _download_run, _download_params),
    "validate": StageDef("validate", "media", validate.run, validate.params),
    "proxy": StageDef("proxy", "media", proxy.run, proxy.params),
    "waveform": StageDef("waveform", "media", waveform.run, waveform.params),
    "thumbnail": StageDef("thumbnail", "media", thumbnail.run, thumbnail.params),
    "extract_audio": StageDef("extract_audio", "media", extract_audio.run, extract_audio.params),
    "asr_prep": StageDef("asr_prep", "media", asr_prep.run, asr_prep.params),
    "transcribe": StageDef("transcribe", "asr", _transcribe_run, _transcribe_params),
    "detect_scenes": StageDef("detect_scenes", "media", detect_scenes.run, detect_scenes.params),
    "highlights": StageDef("highlights", "llm", _highlights_run, _highlights_params),
    "clip_subtitles": StageDef("clip_subtitles", "media", clip_subtitles.run, clip_subtitles.params),
    "clip_loudness": StageDef("clip_loudness", "media", clip_loudness.run, clip_loudness.params),
    "clip_render": StageDef("clip_render", "media", clip_render.run, clip_render.params),
    "clip_thumbnail": StageDef("clip_thumbnail", "media", clip_thumbnail.run, clip_thumbnail.params),
    "clip_qc": StageDef("clip_qc", "media", clip_qc.run, clip_qc.params),
}

COMMON_STAGES = ["validate", "proxy", "waveform", "thumbnail"]
TRANSCRIBE_STAGES = ["extract_audio", "asr_prep", "transcribe"]
HIGHLIGHT_STAGES = ["detect_scenes", "highlights"]
RENDER_STAGES = ["clip_subtitles", "clip_loudness", "clip_render", "clip_thumbnail", "clip_qc"]
PREVIEW_STAGES = ["clip_subtitles", "clip_render", "clip_qc"]

ANALYZE = "analyze_source"
TRANSCRIBE = "transcribe_source"
FIND_HIGHLIGHTS = "find_highlights"
RENDER_CLIP = "render_clip"
RENDER_PREVIEW = "render_preview"


def stage_names_for_job(job_kind: str, source_kind: str) -> list[str]:
    if job_kind == TRANSCRIBE:
        return list(TRANSCRIBE_STAGES)
    if job_kind == FIND_HIGHLIGHTS:
        return list(HIGHLIGHT_STAGES)
    if job_kind == RENDER_CLIP:
        return list(RENDER_STAGES)
    if job_kind == RENDER_PREVIEW:
        return list(PREVIEW_STAGES)
    return COMMON_STAGES if source_kind == "upload" else ["download", *COMMON_STAGES]


def queue_for(stage: str, source_kind: str) -> str:
    # yt-dlp downloads run only in the optional, isolated ingest-worker.
    if stage == "download" and source_kind == "ytdlp":
        return "ingest"
    return STAGES[stage].queue
