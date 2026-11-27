from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.media import JobOut


class TranscribeIn(BaseModel):
    language: str | None = Field(
        default=None, max_length=12, description="Whisper language code, or empty/'auto' to detect"
    )
    model: str | None = Field(default=None, max_length=40)
    force: bool = Field(default=False, description="Recognise again even if an identical result exists")


class TranscriptMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    version: int
    kind: Literal["asr", "edit", "restore"]
    is_current: bool
    engine: str
    model: str
    language: str | None
    language_probability: float | None
    requested_language: str | None
    duration_s: float
    segment_count: int
    word_count: int
    based_on_version: int | None
    created_at: datetime


class WordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idx: int
    start_ms: int
    end_ms: int
    text: str
    probability: float | None


class SegmentOut(BaseModel):
    id: uuid.UUID
    idx: int
    start_ms: int
    end_ms: int
    text: str
    edited: bool
    words: list[WordOut]


class TranscriptOut(BaseModel):
    transcript: TranscriptMeta
    offset: int
    segments: list[SegmentOut]


class TranscribeOut(BaseModel):
    reused: bool
    reason: str
    transcript: TranscriptMeta | None = None
    job: JobOut | None = None


class SegmentEdit(BaseModel):
    segment_idx: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)


class EditIn(BaseModel):
    base_version: int = Field(ge=1)
    edits: list[SegmentEdit] = Field(min_length=1, max_length=200)


class RestoreIn(BaseModel):
    version: int = Field(ge=1)


class AsrSettingsIn(BaseModel):
    model: str = Field(max_length=40)
    language: str | None = Field(default=None, max_length=12)


class AsrModelOut(BaseModel):
    name: str
    mb: int
    multilingual: bool
    note: str
    downloaded: bool


class AsrStatusOut(BaseModel):
    available: bool  # the transcription service is running
    device: str | None
    compute_type: str | None
    default_model: str
    default_language: str | None
    models: list[AsrModelOut]
