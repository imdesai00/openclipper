"""Highlights: request a run, store it, list it, review it (SPEC section 6.E).

A run belongs to one immutable transcript version, so the words a candidate points at can never change under it.
Nothing here calls a model: it creates a job, and the `llm` worker does the calling.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AiModel,
    HighlightCandidate,
    HighlightRun,
    ProcessingJob,
    Setting,
    TaskRoute,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
    User,
    VideoSource,
)
from app.errors import AppError, conflict, not_found
from app.ids import uuid7
from app.pipeline.registry import FIND_HIGHLIGHTS
from app.services import audit, jobs, sources
from app.services import highlight_logic as hl
from app.services.access import Need
from app.services.transcripts import current as current_transcript

LLM_HEARTBEAT = "llm:heartbeat"
TASK = "highlight"
ENGINE_VERSION = 1  # bump when the algorithm changes in a way that should not reuse old runs


# ------------------------------------------------------------------------------------------------ settings (data)
async def get_settings(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(select(Setting).where(Setting.scope == "global", Setting.key == "highlights"))
    ).scalar_one_or_none()
    return hl.merged_settings(row.value if row else None)


async def save_settings(session: AsyncSession, actor: User, values: dict[str, Any], ip: str | None) -> dict[str, Any]:
    merged = hl.merged_settings(values)
    w = merged["weights"]
    if any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
        raise AppError("invalid_weights", "Weights cannot be negative, and at least one must be above zero.", 400)
    if not (0 < merged["min_seconds"] < merged["max_seconds"] <= 600):
        raise AppError("invalid_duration", "Minimum must be above zero and below the maximum (at most 600 s).", 400)
    if not 0 <= merged["window_overlap"] < 0.9 or not 0 < merged["max_overlap"] <= 1:
        raise AppError("invalid_overlap", "Overlap values are out of range.", 400)
    row = (
        await session.execute(
            select(Setting).where(Setting.scope == "global", Setting.key == "highlights").with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(Setting(scope="global", key="highlights", value=merged))
    else:
        row.value = merged
    audit.record(session, "highlights.settings_changed", actor_id=actor.id, details={"weights": w}, ip=ip)
    await session.commit()
    return merged


# ------------------------------------------------------------------------------------------------ reading a transcript
async def load_view(session: AsyncSession, transcript_id: uuid.UUID) -> hl.Transcript:
    t = await session.get(Transcript, transcript_id)
    if t is None:
        raise not_found("Transcript")
    segs = list(
        (
            await session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript_id)
                .order_by(TranscriptSegment.idx)
            )
        ).scalars()
    )
    words: dict[uuid.UUID, list[TranscriptWord]] = {s.id: [] for s in segs}
    if segs:
        q = (
            select(TranscriptWord)
            .where(TranscriptWord.segment_id.in_(list(words)))
            .order_by(TranscriptWord.segment_id, TranscriptWord.idx)
        )
        for w in (await session.execute(q)).scalars():
            words[w.segment_id].append(w)
    rows = [
        (s.idx, s.start_ms, s.end_ms, s.text, [(w.idx, w.text, w.start_ms, w.end_ms) for w in words[s.id]])
        for s in segs
    ]
    return hl.build_transcript(rows, round(t.duration_s * 1000))


# ------------------------------------------------------------------------------------------------ request
def _normalise(body: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    prompt = str(body.get("prompt") or "").strip()
    if len(prompt) > 1000:
        raise AppError("invalid_prompt", "Describe what to look for in 1000 characters or fewer.", 400)
    cats = body.get("categories") or []
    bad = [c for c in cats if c not in hl.CATEGORIES]
    if bad:
        raise AppError("invalid_category", f"Unknown category: {bad[0]}", 400)
    min_s = int(body.get("min_seconds") or cfg["min_seconds"])
    max_s = int(body.get("max_seconds") or cfg["max_seconds"])
    if not (5 <= min_s < max_s <= 600):
        raise AppError("invalid_duration", "Clip length must be at least 5 s, and the minimum below the maximum.", 400)
    n = int(body.get("max_candidates") or cfg["max_candidates"])
    if not 1 <= n <= 30:
        raise AppError("invalid_count", "Ask for between 1 and 30 highlights.", 400)
    return {
        "prompt": prompt,
        "categories": sorted(set(cats)),
        "min_seconds": min_s,
        "max_seconds": max_s,
        "max_candidates": n,
        "model_id": str(body["model_id"]) if body.get("model_id") else None,
    }


async def _route_ids(session: AsyncSession, model_id: str | None) -> list[str]:
    if model_id:
        m = await session.get(AiModel, uuid.UUID(model_id))
        if m is None:
            raise AppError("invalid_model", "That model does not exist.", 400)
        return [model_id]
    ids = (
        (await session.execute(select(TaskRoute.model_id).where(TaskRoute.task == TASK).order_by(TaskRoute.position)))
        .scalars()
        .all()
    )
    if not ids:
        raise AppError("no_model_configured", "Choose a model for highlight detection in the AI settings first.", 409)
    return [str(i) for i in ids]


def params_hash_of(transcript_id: uuid.UUID, request: dict[str, Any], cfg: dict[str, Any], route: list[str]) -> str:
    blob = json.dumps(
        {"v": ENGINE_VERSION, "transcript": str(transcript_id), "request": request, "settings": cfg, "route": route},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


class Requested:
    def __init__(self, run: HighlightRun | None = None, job: ProcessingJob | None = None) -> None:
        self.run, self.job = run, job

    @property
    def reused(self) -> bool:
        return self.run is not None


async def request_run(
    session: AsyncSession,
    redis: Redis,
    user: User,
    source_id: uuid.UUID,
    body: dict[str, Any],
    ip: str | None,
) -> Requested:
    src = await sources.get_source(session, user, source_id, Need.WRITE)
    if src.status != "ready":
        raise AppError("source_not_ready", "Wait until this video has finished processing.", 409)
    t = await current_transcript(session, src.id)
    if t is None:
        raise AppError("no_transcript", "Transcribe this video first; highlights are found in its transcript.", 409)
    cfg = await get_settings(session)
    request = _normalise(body, cfg)
    route = await _route_ids(session, request["model_id"])
    phash = params_hash_of(t.id, request, cfg, route)

    prior = (
        await session.execute(
            select(HighlightRun)
            .where(HighlightRun.source_id == src.id, HighlightRun.params_hash == phash)
            .order_by(HighlightRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior is not None and not body.get("force"):
        return Requested(run=prior)

    if not await redis.exists(LLM_HEARTBEAT):
        raise AppError("llm_unavailable", "The highlight worker is not running. Start the 'worker-llm' service.", 503)
    active = (
        await session.execute(
            select(ProcessingJob.id)
            .where(
                ProcessingJob.source_id == src.id,
                ProcessingJob.kind == FIND_HIGHLIGHTS,
                ProcessingJob.status.in_(("queued", "running")),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise conflict("job_active", "Highlights are already being found for this video.")
    job_params: dict[str, object] = {
        "transcript_id": str(t.id),
        "request": request,
        "settings": cfg,
        "params_hash": phash if not body.get("force") else f"{phash}:{uuid.uuid4().hex}",
        "user_id": str(user.id),
    }
    job = await jobs.create_job(session, src, user.id, kind=FIND_HIGHLIGHTS, params=job_params)
    audit.record(
        session,
        "highlights.requested",
        actor_id=user.id,
        target_type="source",
        target_id=str(src.id),
        details={"transcript_version": t.version, "max_candidates": request["max_candidates"]},
        ip=ip,
    )
    await session.commit()
    jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), src.kind)
    return Requested(job=job)


# ------------------------------------------------------------------------------------------------ store (runner hook)
def _fallback_title(text: str) -> str:
    return " ".join(text.split()[:8]).rstrip(".,;:!?") or "Untitled clip"


async def persist_run(
    session: AsyncSession, *, source: VideoSource, job: ProcessingJob, data: hl.RunData
) -> HighlightRun:
    assert data.transcript is not None
    t = data.transcript
    stored_hash = data.params_hash.split(":", 1)[0]
    run = HighlightRun(
        id=uuid7(),
        source_id=source.id,
        transcript_id=uuid.UUID(data.transcript_id),
        params_hash=stored_hash,
        params=data.params,
        stats=data.stats,
        job_id=job.id,
        created_by=job.requested_by,
    )
    session.add(run)
    await session.flush()
    for rank, sc in enumerate(data.candidates, start=1):
        r = sc.resolved
        a, b = t.words[r.start_pos], t.words[r.end_pos]
        text = t.text_of(r.start_pos, r.end_pos)
        session.add(
            HighlightCandidate(
                id=uuid7(),
                run_id=run.id,
                rank=rank,
                start_seg=a.seg,
                start_word=a.idx,
                end_seg=b.seg,
                end_word=b.idx,
                start_ms=r.start_ms,
                end_ms=r.end_ms,
                category=r.raw.category,
                hook=(r.raw.hook or _fallback_title(text))[:400],
                title=(r.raw.title or _fallback_title(text))[:200],
                explanation=r.raw.explanation,
                llm_score=sc.llm,
                standalone_score=sc.standalone,
                confidence=sc.confidence,
                final_score=sc.final,
                score_parts=sc.parts,
                provider_name=sc.provider_name[:100],
                model_name=sc.model_name[:200],
            )
        )
    await session.flush()
    return run


# ------------------------------------------------------------------------------------------------ read and review
async def candidate_texts(
    session: AsyncSession, transcript_id: uuid.UUID, cands: list[HighlightCandidate]
) -> dict[uuid.UUID, str]:
    """The words a candidate covers, read back from the stored transcript. This is the ONLY source of its text."""
    if not cands:
        return {}
    lo, hi = min(c.start_seg for c in cands), max(c.end_seg for c in cands)
    segs = {
        s.id: s.idx
        for s in (
            await session.execute(
                select(TranscriptSegment).where(
                    TranscriptSegment.transcript_id == transcript_id, TranscriptSegment.idx.between(lo, hi)
                )
            )
        ).scalars()
    }
    by_seg: dict[int, list[tuple[int, str]]] = {i: [] for i in segs.values()}
    if segs:
        for w in (
            await session.execute(select(TranscriptWord).where(TranscriptWord.segment_id.in_(list(segs))))
        ).scalars():
            by_seg[segs[w.segment_id]].append((w.idx, w.text))
    out: dict[uuid.UUID, str] = {}
    for c in cands:
        picked: list[str] = []
        for si in range(c.start_seg, c.end_seg + 1):
            for wi, text in sorted(by_seg.get(si, [])):
                if (si, wi) >= (c.start_seg, c.start_word) and (si, wi) <= (c.end_seg, c.end_word):
                    picked.append(text.strip())
        out[c.id] = " ".join(picked)
    return out


async def list_runs(session: AsyncSession, user: User, source_id: uuid.UUID) -> list[tuple[HighlightRun, int]]:
    await sources.get_source(session, user, source_id, Need.READ)
    rows = (
        await session.execute(
            select(HighlightRun, func.count(HighlightCandidate.id))
            .outerjoin(HighlightCandidate, HighlightCandidate.run_id == HighlightRun.id)
            .where(HighlightRun.source_id == source_id)
            .group_by(HighlightRun.id)
            .order_by(HighlightRun.created_at.desc())
        )
    ).all()
    return [(r, int(n)) for r, n in rows]


async def get_run(
    session: AsyncSession, user: User, source_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[HighlightRun, list[tuple[HighlightCandidate, str]]]:
    await sources.get_source(session, user, source_id, Need.READ)
    run = await session.get(HighlightRun, run_id)
    if run is None or run.source_id != source_id:
        raise not_found("Highlight run")
    cands = list(
        (
            await session.execute(
                select(HighlightCandidate).where(HighlightCandidate.run_id == run.id).order_by(HighlightCandidate.rank)
            )
        ).scalars()
    )
    texts = await candidate_texts(session, run.transcript_id, cands)
    return run, [(c, texts[c.id]) for c in cands]


async def latest_run(session: AsyncSession, source_id: uuid.UUID) -> tuple[HighlightRun, int] | None:
    row = (
        await session.execute(
            select(HighlightRun, func.count(HighlightCandidate.id))
            .outerjoin(HighlightCandidate, HighlightCandidate.run_id == HighlightRun.id)
            .where(HighlightRun.source_id == source_id)
            .group_by(HighlightRun.id)
            .order_by(HighlightRun.created_at.desc())
            .limit(1)
        )
    ).first()
    return (row[0], int(row[1])) if row else None


async def set_status(
    session: AsyncSession, user: User, candidate_id: uuid.UUID, status: str, ip: str | None
) -> tuple[HighlightCandidate, str]:
    if status not in ("new", "accepted", "rejected"):
        raise AppError("invalid_status", "Status must be new, accepted or rejected.", 400)
    c = await session.get(HighlightCandidate, candidate_id)
    run = await session.get(HighlightRun, c.run_id) if c else None
    if c is None or run is None:
        raise not_found("Highlight")
    await sources.get_source(session, user, run.source_id, Need.WRITE)
    c.status = status
    c.reviewed_by, c.reviewed_at = (None, None) if status == "new" else (user.id, datetime.now(UTC))
    audit.record(
        session,
        "highlights.reviewed",
        actor_id=user.id,
        target_type="highlight",
        target_id=str(c.id),
        details={"status": status},
        ip=ip,
    )
    await session.commit()
    texts = await candidate_texts(session, run.transcript_id, [c])
    return c, texts[c.id]
