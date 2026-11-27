from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.ports.asr import TranscriptData
from app.ports.storage import StorageBackend
from app.services.highlight_logic import RunData


class StageError(Exception):
    """A stage failed for a reason the user should see (never a raw traceback)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SourceInfo:
    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    origin_url: str | None
    original_filename: str | None
    original_key: str | None
    duration_s: float | None
    width: int | None
    height: int | None
    has_audio: bool | None
    has_video: bool = True
    media_hash: str | None = None
    proxy_key: str | None = None  # the editing copy: cheaper than the original for analysis that only looks
    waveform_key: str | None = None


@dataclass
class OutputFile:
    kind: str  # media_assets.kind
    path: Path
    mime: str
    source_field: str | None  # video_sources.<field> to point at the new asset (None: only the stage output)
    ext: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None


@dataclass
class StageResult:
    outputs: list[OutputFile] = field(default_factory=list)
    source_updates: dict[str, Any] = field(default_factory=dict)
    transcript: TranscriptData | None = None  # persisted by the runner as a new transcript version
    highlights: RunData | None = None  # persisted by the runner as a highlight run with its candidates


@dataclass
class StageContext:
    settings: Settings
    storage: StorageBackend
    source: SourceInfo
    workdir: Path
    progress: Callable[[float, str | None], None]
    cancelled: Callable[[], bool]
    job_params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)  # stage name -> storage key of that stage's output in this job
