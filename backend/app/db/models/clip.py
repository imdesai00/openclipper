from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

ASPECTS = ("9:16", "1:1", "16:9")
PLATFORMS = ("generic", "youtube", "instagram")
RENDER_KINDS = ("preview", "final")


class CaptionTemplate(IdMixin, TimestampMixin, Base):
    """A caption look. `style` is the one schema that is compiled to CSS for the editor and to ASS for the render."""

    __tablename__ = "caption_templates"
    __table_args__ = (
        Index("uq_caption_templates_builtin_name", "name", unique=True, postgresql_where=text("is_builtin")),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    style: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class Clip(IdMixin, TimestampMixin, Base):
    """A clip is a name plus a history of versions. Editing never changes a version; it adds one."""

    __tablename__ = "clips"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("highlight_candidates.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class ClipVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "clip_versions"
    __table_args__ = (
        UniqueConstraint("clip_id", "version", name="uq_clip_versions_clip_version"),
        CheckConstraint("end_ms > start_ms", name="ck_clip_versions_span"),
        CheckConstraint("aspect in ('9:16', '1:1', '16:9')", name="ck_clip_versions_aspect"),
    )

    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    aspect: Mapped[str] = mapped_column(String(5), nullable=False)
    crop: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # mode, zoom, pan keyframes
    background: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    caption_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("caption_templates.id", ondelete="SET NULL"), index=True
    )
    caption_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    audio: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # the transcript version the captions were made from, so a render can be reproduced after transcript edits
    transcript_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transcripts.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class ClipRender(IdMixin, TimestampMixin, Base):
    """One rendered file for one version: a quick low-resolution preview, or the final export."""

    __tablename__ = "clip_renders"
    __table_args__ = (
        CheckConstraint("kind in ('preview', 'final')", name="ck_clip_renders_kind"),
        CheckConstraint("status in ('pending', 'done')", name="ck_clip_renders_status"),
        CheckConstraint("qc_status in ('pass', 'warn', 'fail')", name="ck_clip_renders_qc_status"),
        Index("ix_clip_renders_version_kind_hash", "clip_version_id", "kind", "render_hash"),
    )

    clip_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clip_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="pending")
    # sha256 of everything that changes the picture or sound (version, template, transcript, size, engine)
    render_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id", ondelete="SET NULL"), index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"), index=True)
    thumbnail_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    subtitle_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    loudness_lufs: Mapped[float | None] = mapped_column(Float)  # measured on the finished file, never assumed
    qc_status: Mapped[str | None] = mapped_column(String(4))
    qc_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class ClipMetadata(IdMixin, TimestampMixin, Base):
    """Per clip and per platform, generated independently and always editable."""

    __tablename__ = "clip_metadata"
    __table_args__ = (
        UniqueConstraint("clip_id", "platform", name="uq_clip_metadata_clip_platform"),
        CheckConstraint("platform in ('generic', 'youtube', 'instagram')", name="ck_clip_metadata_platform"),
    )

    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    hook: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    short_description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False, server_default="{}")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False, server_default="{}")
    thumbnail_text: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    category: Mapped[str] = mapped_column(String(60), nullable=False, server_default="")
    audience: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    generated_by: Mapped[str | None] = mapped_column(String(300))  # "provider / model", None when typed by a person
    last_error: Mapped[str | None] = mapped_column(Text)  # why the last attempt to generate this platform failed
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
