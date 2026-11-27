from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.media import JobOut


class FindHighlightsIn(BaseModel):
    prompt: str = Field(default="", max_length=1000, description="What to look for, in plain words")
    categories: list[str] = Field(default_factory=list, max_length=11)
    max_candidates: int | None = Field(default=None, ge=1, le=30)
    min_seconds: int | None = Field(default=None, ge=5, le=600)
    max_seconds: int | None = Field(default=None, ge=5, le=600)
    model_id: uuid.UUID | None = None
    force: bool = Field(default=False, description="Ask the model again even if an identical run exists")


class ScorePartOut(BaseModel):
    value: float
    weight: float


class CandidateOut(BaseModel):
    id: uuid.UUID
    rank: int
    start_ms: int
    end_ms: int
    duration_ms: int
    text: str  # always read back from the stored transcript, never from the model
    category: str
    hook: str
    title: str
    explanation: str
    llm_score: int
    standalone_score: int
    confidence: int
    final_score: float
    score_parts: dict[str, Any]
    provider_name: str
    model_name: str
    status: Literal["new", "accepted", "rejected"]


class RunSummary(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    transcript_id: uuid.UUID
    created_at: datetime
    candidate_count: int
    request: dict[str, Any]
    stats: dict[str, Any]


class RunOut(BaseModel):
    run: RunSummary
    candidates: list[CandidateOut]
    score_note: str = "AI relevance score, not an engagement prediction."


class FindHighlightsOut(BaseModel):
    reused: bool
    run: RunSummary | None = None
    job: JobOut | None = None


class ReviewIn(BaseModel):
    status: Literal["new", "accepted", "rejected"]


class HighlightSettingsOut(BaseModel):
    settings: dict[str, Any]
    categories: list[str]
