"""Speech recognition port (SPEC §5.1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ASRError(Exception):
    """Recognition failed for a reason the user should see. Never carries a raw traceback."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class WordData:
    start: float  # seconds
    end: float
    text: str
    probability: float | None = None


@dataclass
class SegmentData:
    start: float
    end: float
    text: str
    words: list[WordData] = field(default_factory=list)


@dataclass
class TranscriptData:
    language: str
    language_probability: float | None
    duration_s: float
    engine: str
    model: str
    segments: list[SegmentData]


class TranscriptionEngine(Protocol):
    name: str

    def is_model_available(self, model: str) -> bool: ...

    def ensure_model(self, model: str, progress: Callable[[float, str | None], None]) -> None:
        """Download the model if it is not on disk, reporting progress 0..1."""

    def transcribe(
        self,
        audio: Path,
        *,
        model: str,
        language: str | None,
        vad: bool,
        progress: Callable[[float, str | None], None],
        cancelled: Callable[[], bool],
    ) -> TranscriptData:
        """Recognise a 16 kHz mono 16-bit WAV file."""
