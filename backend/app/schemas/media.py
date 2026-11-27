from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectDeleteIn(BaseModel):
    confirm_name: str


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID
    created_at: datetime
    my_role: Literal["owner", "editor", "viewer"]
    source_count: int = 0


class MemberIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["editor", "viewer"]


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: Literal["owner", "editor", "viewer"]


class LinkSourceIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    attested: bool = False


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stage: str
    position: int
    status: str
    progress: float
    message: str | None
    attempts: int
    error_code: str | None
    error_message: str | None


class JobOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    source_id: uuid.UUID | None
    kind: str
    status: str
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    stages: list[StageOut]


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    title: str
    origin_url: str | None
    original_filename: str | None
    status: str
    error_code: str | None
    error_message: str | None
    size_bytes: int | None
    duration_s: float | None
    width: int | None
    height: int | None
    container: str | None
    video_codec: str | None
    audio_codec: str | None
    has_audio: bool | None
    has_video: bool = True
    created_at: datetime
    thumbnail_url: str | None = None


class SourceDetail(SourceOut):
    proxy_url: str | None = None
    waveform_url: str | None = None
    media_hash: str | None = None
    scenes_url: str | None = None  # scene cuts (JSON) for the editor timeline, once they have been found
    job: JobOut | None = None  # the latest analysis job
    transcription_job: JobOut | None = None
    transcript: dict[str, object] | None = None  # summary of the current transcript, if any
    highlights_job: JobOut | None = None
    highlights: dict[str, object] | None = None  # the latest highlight run: id, when, how many


class IngestSettingsIn(BaseModel):
    enabled: bool
    daily_limit: int = Field(ge=1, le=1000)


class FeaturesOut(BaseModel):
    ingest_enabled: bool  # admin switch
    ingest_available: bool  # switch on AND the ingest worker is running
    ingest_daily_limit: int
    max_upload_bytes: int
    allowed_extensions: list[str]
