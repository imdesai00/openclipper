from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.ports.storage import StorageBackend


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


@dataclass
class OutputFile:
    kind: str  # media_assets.kind
    path: Path
    mime: str
    source_field: str  # video_sources.<field>_id to point at the new asset
    ext: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None


@dataclass
class StageResult:
    outputs: list[OutputFile] = field(default_factory=list)
    source_updates: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageContext:
    settings: Settings
    storage: StorageBackend
    source: SourceInfo
    workdir: Path
    progress: Callable[[float, str | None], None]
    cancelled: Callable[[], bool]
