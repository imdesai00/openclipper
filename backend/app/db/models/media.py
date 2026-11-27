from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

SOURCE_KINDS = ("upload", "direct_url", "ytdlp")
SOURCE_STATUSES = ("pending", "processing", "ready", "failed")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
STAGE_STATUSES = ("pending", "running", "succeeded", "skipped", "failed", "cancelled")
MEMBER_ROLES = ("owner", "editor", "viewer")
ASSET_KINDS = ("original", "proxy", "waveform", "thumbnail", "audio", "audio_prep", "scenes")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} in ({', '.join(repr(v) for v in values)})"


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)


class ProjectMember(IdMixin, TimestampMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members"),
        CheckConstraint(_in("role", MEMBER_ROLES), name="ck_project_members_role"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)


class MediaAsset(IdMixin, TimestampMixin, Base):
    """A stored file. Only metadata lives here; bytes live in the storage backend."""

    __tablename__ = "media_assets"
    __table_args__ = (CheckConstraint(_in("kind", ASSET_KINDS), name="ck_media_assets_kind"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoSource(IdMixin, TimestampMixin, Base):
    __tablename__ = "video_sources"
    __table_args__ = (
        CheckConstraint(_in("kind", SOURCE_KINDS), name="ck_video_sources_kind"),
        CheckConstraint(_in("status", SOURCE_STATUSES), name="ck_video_sources_status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    origin_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)

    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    media_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    duration_s: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    container: Mapped[str | None] = mapped_column(String(100))
    video_codec: Mapped[str | None] = mapped_column(String(50))
    audio_codec: Mapped[str | None] = mapped_column(String(50))
    has_audio: Mapped[bool | None] = mapped_column(Boolean)
    has_video: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    original_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    proxy_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    waveform_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    thumbnail_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    audio_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    scenes_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class SourcePermission(IdMixin, TimestampMixin, Base):
    """The user's attestation that they own or are licensed to use the content (SPEC §6.B)."""

    __tablename__ = "source_permissions"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_sources.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    statement_text: Mapped[str] = mapped_column(Text, nullable=False)


class ProcessingJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (CheckConstraint(_in("status", JOB_STATUSES), name="ck_processing_jobs_status"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("video_sources.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobStage(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_stages"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", name="uq_job_stages_job_stage"),
        CheckConstraint(_in("status", STAGE_STATUSES), name="ck_job_stages_status"),
        Index("ix_job_stages_stage_params", "stage", "params_hash"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    params_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    progress: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    message: Mapped[str | None] = mapped_column(Text)
    output_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    reused_from: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_stages.id", ondelete="SET NULL"), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class AssetLease(IdMixin, TimestampMixin, Base):
    """Assets referenced by a running job must not be deleted by retention (SPEC §5.5)."""

    __tablename__ = "asset_leases"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Upload(IdMixin, TimestampMixin, Base):
    """State of a resumable (tus) upload. Bytes are staged on disk until complete."""

    __tablename__ = "uploads"
    __table_args__ = (CheckConstraint(_in("status", ("active", "completed", "failed")), name="ck_uploads_status"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("video_sources.id", ondelete="SET NULL"), index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    upload_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_offset: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    staging_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
