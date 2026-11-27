from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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

TRANSCRIPT_KINDS = ("asr", "edit", "restore")


class Transcript(IdMixin, TimestampMixin, Base):
    """One immutable version of a source's transcript. Editing never changes a row, it adds a new version."""

    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_transcripts_source_version"),
        CheckConstraint("kind in ('asr', 'edit', 'restore')", name="ck_transcripts_kind"),
        # at most one current version per source
        Index("uq_transcripts_one_current", "source_id", unique=True, postgresql_where=text("is_current")),
        Index("ix_transcripts_cache_key", "cache_key"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    engine: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    language: Mapped[str | None] = mapped_column(String(12))  # what was detected or requested
    language_probability: Mapped[float | None] = mapped_column(Float)
    requested_language: Mapped[str | None] = mapped_column(String(12))  # None = auto-detect
    # sha256(media hash, engine, model, requested language, preprocessing). Equal keys mean equal results.
    cache_key: Mapped[str | None] = mapped_column(String(64))
    options: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    based_on_version: Mapped[int | None] = mapped_column(Integer)  # for edits and restores
    cloned_from: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transcripts.id", ondelete="SET NULL"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id", ondelete="SET NULL"), index=True)


class Speaker(IdMixin, TimestampMixin, Base):
    """Reserved for diarization (not built yet). Without it every segment has speaker_id NULL (one speaker)."""

    __tablename__ = "speakers"

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))


class TranscriptSegment(IdMixin, Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("transcript_id", "idx", name="uq_segments_transcript_idx"),)

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("speakers.id", ondelete="SET NULL"), index=True)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class TranscriptWord(IdMixin, Base):
    __tablename__ = "transcript_words"
    __table_args__ = (UniqueConstraint("segment_id", "idx", name="uq_words_segment_idx"),)

    segment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(200), nullable=False)
    probability: Mapped[float | None] = mapped_column(Float)


_ = datetime  # (kept for typing of future audit columns)
