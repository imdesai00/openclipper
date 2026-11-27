from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import CurrentUser, DbDep, RedisDep, client_ip, require_role
from app.api.presenters import job_out
from app.db.models import HighlightCandidate, HighlightRun, User
from app.schemas.highlight import (
    CandidateOut,
    FindHighlightsIn,
    FindHighlightsOut,
    HighlightSettingsOut,
    ReviewIn,
    RunOut,
    RunSummary,
)
from app.services import highlight_logic as hl
from app.services import highlights as svc
from app.services import jobs as jobs_svc

router = APIRouter(tags=["highlights"])
AdminDep = Annotated[User, Depends(require_role("admin"))]


def _summary(run: HighlightRun, n: int) -> RunSummary:
    return RunSummary(
        id=run.id,
        source_id=run.source_id,
        transcript_id=run.transcript_id,
        created_at=run.created_at,
        candidate_count=n,
        request=(run.params or {}).get("request", {}),
        stats=run.stats,
    )


def _cand(c: HighlightCandidate, text: str) -> CandidateOut:
    return CandidateOut(
        id=c.id,
        rank=c.rank,
        start_ms=c.start_ms,
        end_ms=c.end_ms,
        duration_ms=c.end_ms - c.start_ms,
        text=text,
        category=c.category,
        hook=c.hook,
        title=c.title,
        explanation=c.explanation,
        llm_score=c.llm_score,
        standalone_score=c.standalone_score,
        confidence=c.confidence,
        final_score=c.final_score,
        score_parts=c.score_parts,
        provider_name=c.provider_name,
        model_name=c.model_name,
        status=c.status,
    )


@router.post("/sources/{source_id}/highlights", response_model=FindHighlightsOut)
async def find_highlights(
    source_id: uuid.UUID,
    body: FindHighlightsIn,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: DbDep,
    redis: RedisDep,
) -> FindHighlightsOut:
    r = await svc.request_run(session, redis, user, source_id, body.model_dump(mode="json"), client_ip(request))
    if r.run is not None:
        runs = {x.id: n for x, n in await svc.list_runs(session, user, source_id)}
        return FindHighlightsOut(reused=True, run=_summary(r.run, runs.get(r.run.id, 0)))
    response.status_code = 202
    assert r.job is not None
    return FindHighlightsOut(reused=False, job=job_out(*(await jobs_svc.get_job(session, r.job.id))))


@router.get("/sources/{source_id}/highlights", response_model=list[RunSummary])
async def list_runs(source_id: uuid.UUID, user: CurrentUser, session: DbDep) -> list[RunSummary]:
    return [_summary(r, n) for r, n in await svc.list_runs(session, user, source_id)]


@router.get("/sources/{source_id}/highlights/{run_id}", response_model=RunOut)
async def get_run(source_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, session: DbDep) -> RunOut:
    run, items = await svc.get_run(session, user, source_id, run_id)
    return RunOut(run=_summary(run, len(items)), candidates=[_cand(c, t) for c, t in items])


@router.patch("/highlights/{candidate_id}", response_model=CandidateOut)
async def review(
    candidate_id: uuid.UUID, body: ReviewIn, request: Request, user: CurrentUser, session: DbDep
) -> CandidateOut:
    c, text = await svc.set_status(session, user, candidate_id, body.status, client_ip(request))
    return _cand(c, text)


@router.get("/highlights/settings", response_model=HighlightSettingsOut)
async def get_settings(_: AdminDep, session: DbDep) -> HighlightSettingsOut:
    return HighlightSettingsOut(settings=await svc.get_settings(session), categories=list(hl.CATEGORIES))


@router.put("/highlights/settings", response_model=HighlightSettingsOut)
async def put_settings(body: dict[str, Any], request: Request, admin: AdminDep, session: DbDep) -> HighlightSettingsOut:
    saved = await svc.save_settings(session, admin, body, client_ip(request))
    return HighlightSettingsOut(settings=saved, categories=list(hl.CATEGORIES))
