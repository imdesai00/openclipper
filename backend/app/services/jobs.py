from __future__ import annotations

import uuid
from datetime import UTC, datetime

from redis import Redis as SyncRedis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobStage, ProcessingJob, VideoSource
from app.errors import AppError
from app.pipeline.registry import ANALYZE, queue_for, stage_names_for_job
from app.pipeline.runner import CANCEL_KEY, request_cancel
from app.services import audit
from app.tasks.dispatch import dispatch_job


def _now() -> datetime:
    return datetime.now(UTC)


async def create_job(
    session: AsyncSession,
    source: VideoSource,
    user_id: uuid.UUID | None,
    *,
    kind: str = ANALYZE,
    params: dict[str, object] | None = None,
) -> ProcessingJob:
    job = ProcessingJob(
        project_id=source.project_id,
        source_id=source.id,
        kind=kind,
        status="queued",
        requested_by=user_id,
        params=params,
    )
    session.add(job)
    await session.flush()
    for i, name in enumerate(stage_names_for_job(kind, source.kind)):
        session.add(JobStage(job_id=job.id, stage=name, position=i, status="pending"))
    await session.flush()
    return job


async def create_analyze_job(session: AsyncSession, source: VideoSource, user_id: uuid.UUID | None) -> ProcessingJob:
    return await create_job(session, source, user_id, kind=ANALYZE)


async def pending_stages(session: AsyncSession, job_id: uuid.UUID) -> list[str]:
    rows = (
        await session.execute(
            select(JobStage.stage)
            .where(JobStage.job_id == job_id, JobStage.status.notin_(("succeeded", "skipped")))
            .order_by(JobStage.position)
        )
    ).scalars()
    return list(rows)


def dispatch(job_id: uuid.UUID, stages: list[str], source_kind: str) -> None:
    dispatch_job(job_id, [(s, queue_for(s, source_kind)) for s in stages])


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> tuple[ProcessingJob, list[JobStage]]:
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        raise AppError("not_found", "Job not found", 404)
    stages = list(
        (await session.execute(select(JobStage).where(JobStage.job_id == job_id).order_by(JobStage.position))).scalars()
    )
    return job, stages


async def retry_job(
    session: AsyncSession, redis: SyncRedis, job: ProcessingJob, actor: uuid.UUID, ip: str | None
) -> None:
    if job.status not in ("failed", "cancelled"):
        raise AppError("not_retryable", "Only failed or cancelled jobs can be retried", 409)
    source = await session.get(VideoSource, job.source_id) if job.source_id else None
    if source is None:
        raise AppError("not_found", "Source not found", 404)
    redis.delete(CANCEL_KEY.format(job.id))
    await session.execute(
        update(JobStage)
        .where(JobStage.job_id == job.id, JobStage.status.in_(("failed", "cancelled", "running")))
        .values(status="pending", error_code=None, error_message=None, progress=0.0, message=None)
    )
    job.status, job.error_code, job.error_message, job.finished_at = "queued", None, None, None
    if job.kind == ANALYZE:
        source.status, source.error_code, source.error_message = "pending", None, None
    audit.record(session, "job.retried", actor_id=actor, target_type="job", target_id=str(job.id), ip=ip)
    await session.commit()
    dispatch(job.id, await pending_stages(session, job.id), source.kind)  # finished stages are skipped


async def cancel_job(
    session: AsyncSession, redis: SyncRedis, job: ProcessingJob, actor: uuid.UUID, ip: str | None
) -> None:
    if job.status not in ("queued", "running"):
        raise AppError("not_cancellable", "This job is not running", 409)
    request_cancel(redis, job.id)  # a running ffmpeg notices within about a second
    audit.record(
        session,
        "job.cancel_requested",
        actor_id=actor,
        target_type="job",
        target_id=str(job.id),
        ip=ip,
    )
    if job.status == "queued":  # nothing running yet: cancel right away
        job.status, job.finished_at = "cancelled", _now()
        await session.execute(
            update(JobStage).where(JobStage.job_id == job.id, JobStage.status == "pending").values(status="cancelled")
        )
        if job.source_id and job.kind == ANALYZE:
            await session.execute(
                update(VideoSource)
                .where(VideoSource.id == job.source_id)
                .values(status="failed", error_code="cancelled", error_message="Cancelled")
            )
    await session.commit()


async def active_job_exists(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
) -> bool:
    q = select(ProcessingJob.id).where(ProcessingJob.status.in_(("queued", "running")))
    if project_id:
        q = q.where(ProcessingJob.project_id == project_id)
    if source_id:
        q = q.where(ProcessingJob.source_id == source_id)
    return (await session.execute(q.limit(1))).first() is not None
