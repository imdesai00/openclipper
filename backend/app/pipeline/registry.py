from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.pipeline.stages import proxy, thumbnail, validate, waveform
from app.pipeline.types import StageContext, StageResult


@dataclass(frozen=True)
class StageDef:
    name: str
    queue: str
    run: Callable[[StageContext], StageResult]
    params: Callable[[Settings], dict[str, Any]]


def _download_run(ctx: StageContext) -> StageResult:
    from app.pipeline.stages import download  # imported lazily: needs optional tooling

    return download.run(ctx)


def _download_params(settings: Settings) -> dict[str, Any]:
    from app.pipeline.stages import download

    return download.params(settings)


STAGES: dict[str, StageDef] = {
    "download": StageDef("download", "media", _download_run, _download_params),
    "validate": StageDef("validate", "media", validate.run, validate.params),
    "proxy": StageDef("proxy", "media", proxy.run, proxy.params),
    "waveform": StageDef("waveform", "media", waveform.run, waveform.params),
    "thumbnail": StageDef("thumbnail", "media", thumbnail.run, thumbnail.params),
}

COMMON_STAGES = ["validate", "proxy", "waveform", "thumbnail"]


def stage_names_for(kind: str) -> list[str]:
    return COMMON_STAGES if kind == "upload" else ["download", *COMMON_STAGES]


def queue_for(stage: str, source_kind: str) -> str:
    # yt-dlp downloads run only in the optional, isolated ingest-worker.
    if stage == "download" and source_kind == "ytdlp":
        return "ingest"
    return STAGES[stage].queue
