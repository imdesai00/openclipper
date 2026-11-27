from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response

from app.api.deps import CurrentUser, DbDep, SettingsDep, StorageDep, SyncRedisDep, client_ip
from app.api.presenters import job_out, source_detail
from app.db.models import ProcessingJob
from app.errors import not_found
from app.pipeline import registry as jobs_pipeline
from app.schemas.media import JobOut, SourceDetail
from app.services import jobs as jobs_svc
from app.services import sources as svc
from app.services import transcripts as transcripts_svc
from app.services.access import Need, require_project

router = APIRouter(tags=["sources"])


@router.get("/sources/{source_id}", response_model=SourceDetail)
async def get(source_id: uuid.UUID, user: CurrentUser, session: DbDep, settings: SettingsDep) -> SourceDetail:
    src = await svc.get_source(session, user, source_id)
    job = await svc.latest_job(session, src.id)
    jo = job_out(*(await jobs_svc.get_job(session, job.id))) if job else None
    tj = await svc.latest_job(session, src.id, jobs_pipeline.TRANSCRIBE)
    tjo = job_out(*(await jobs_svc.get_job(session, tj.id))) if tj else None
    t = await transcripts_svc.current(session, src.id)
    summary = (
        {
            "version": t.version,
            "kind": t.kind,
            "language": t.language,
            "model": t.model,
            "segment_count": t.segment_count,
            "word_count": t.word_count,
        }
        if t
        else None
    )
    return source_detail(src, settings, jo, tjo, summary)


@router.post("/sources/{source_id}/reprocess", response_model=JobOut, status_code=202)
async def reprocess(source_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep) -> JobOut:
    job = await svc.reprocess(session, user, source_id, client_ip(request))
    return job_out(*(await jobs_svc.get_job(session, job.id)))


@router.delete("/sources/{source_id}", status_code=204)
async def delete(
    source_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep, storage: StorageDep
) -> Response:
    await svc.delete_source(session, storage, user, source_id, client_ip(request))
    return Response(status_code=204)


async def _job_for(session: DbDep, user: CurrentUser, job_id: uuid.UUID, need: Need) -> ProcessingJob:
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise not_found("Job")
    await require_project(session, user, job.project_id, need)
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, user: CurrentUser, session: DbDep) -> JobOut:
    job = await _job_for(session, user, job_id, Need.READ)
    return job_out(*(await jobs_svc.get_job(session, job.id)))


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=202)
async def retry(job_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep, redis: SyncRedisDep) -> JobOut:
    job = await _job_for(session, user, job_id, Need.WRITE)
    await jobs_svc.retry_job(session, redis, job, user.id, client_ip(request))
    return job_out(*(await jobs_svc.get_job(session, job.id)))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, status_code=202)
async def cancel(job_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep, redis: SyncRedisDep) -> JobOut:
    job = await _job_for(session, user, job_id, Need.WRITE)
    await jobs_svc.cancel_job(session, redis, job, user.id, client_ip(request))
    await session.refresh(job)
    return job_out(*(await jobs_svc.get_job(session, job.id)))
