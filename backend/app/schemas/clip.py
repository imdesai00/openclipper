from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.media import JobOut


class CreateClipIn(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=200)


class VersionIn(BaseModel):
    """Everything the editor can change. Omitted fields keep the base version's value."""

    base_version: int = Field(ge=1)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    aspect: Literal["9:16", "1:1", "16:9"] | None = None
    crop: dict[str, Any] | None = None
    background: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    caption_template_id: uuid.UUID | None = None
    caption_overrides: dict[str, Any] | None = None
    title: str | None = Field(default=None, max_length=200)
    refresh_transcript: bool = False


class VersionOut(BaseModel):
    id: uuid.UUID
    version: int
    start_ms: int
    end_ms: int
    aspect: str
    crop: dict[str, Any]
    background: dict[str, Any]
    audio: dict[str, Any]
    caption_template_id: uuid.UUID | None
    caption_overrides: dict[str, Any]
    transcript_id: uuid.UUID | None
    created_at: datetime


class RenderIn(BaseModel):
    kind: Literal["preview", "final"] = "final"
    version: int | None = Field(default=None, ge=1)
    force: bool = False


class RenderOut(BaseModel):
    id: uuid.UUID
    kind: Literal["preview", "final"]
    version: int
    state: Literal["pending", "running", "done", "failed", "cancelled"]
    error: str | None = None
    created_at: datetime
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    size_bytes: int | None = None
    loudness_lufs: float | None = None
    qc_status: Literal["pass", "warn", "fail"] | None = None
    qc_report: dict[str, Any] | None = None
    play_url: str | None = None
    download_url: str | None = None
    thumbnail_url: str | None = None
    job: JobOut | None = None


class RenderRequestedOut(BaseModel):
    reused: bool
    render: RenderOut


class ClipSummary(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    title: str
    current_version: int
    start_ms: int
    end_ms: int
    aspect: str
    created_at: datetime
    candidate_id: uuid.UUID | None = None
    best_render_state: str | None = None
    thumbnail_url: str | None = None


class ClipOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    source_id: uuid.UUID
    candidate_id: uuid.UUID | None
    title: str
    current_version: int
    version: VersionOut
    style: dict[str, Any]  # the template with this clip's overrides applied: what the render will use
    template_name: str | None
    renders: list[RenderOut]
    created_at: datetime


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    style: dict[str, Any]


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    style: dict[str, Any] | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    is_builtin: bool
    style: dict[str, Any]


class PreviewCaption(BaseModel):
    start_ms: int
    end_ms: int
    lines: list[list[dict[str, Any]]]  # [{text, start_ms, end_ms}] per line


class PreviewStyleOut(BaseModel):
    css: dict[str, Any]
    cues: list[PreviewCaption]
    output_width: int
    output_height: int
    fonts: list[str]
