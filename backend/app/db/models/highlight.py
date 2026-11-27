from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

CANDIDATE_STATUSES = ("new", "accepted", "rejected")


class HighlightRun(IdMixin, TimestampMixin, Base):
    """One request to find highlights in one version of a transcript, with exactly the settings that were used."""

    __tablename__ = "highlight_runs"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # sha256 of everything that changes the result (transcript version, request, scoring settings, model route).
    # An identical request reuses the run instead of paying for the model again.
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # what happened: windows tried and failed, how many raw candidates, why each was dropped, tokens, cost estimate
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id", ondelete="SET NULL"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class HighlightCandidate(IdMixin, TimestampMixin, Base):
    """A suggested clip. Where it starts and ends is a pair of real transcript words; the text is never stored here."""

    __tablename__ = "highlight_candidates"
    __table_args__ = (
        CheckConstraint("status in ('new', 'accepted', 'rejected')", name="ck_highlight_candidates_status"),
        CheckConstraint("end_ms > start_ms", name="ck_highlight_candidates_span"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("highlight_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    # the first and last spoken word, as (segment idx, word idx) in the run's transcript version
    start_seg: Mapped[int] = mapped_column(Integer, nullable=False)
    start_word: Mapped[int] = mapped_column(Integer, nullable=False)
    end_seg: Mapped[int] = mapped_column(Integer, nullable=False)
    end_word: Mapped[int] = mapped_column(Integer, nullable=False)
    # playback range = those words plus padding
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    llm_score: Mapped[int] = mapped_column(Integer, nullable=False)  # what the model said, 0-100
    standalone_score: Mapped[int] = mapped_column(Integer, nullable=False)  # makes sense without the rest, 0-100
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)  # the model's own, 0-100
    final_score: Mapped[float] = mapped_column(Float, nullable=False)  # the weighted ranking score, 0-100
    score_parts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # each component and its weight
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="new")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
