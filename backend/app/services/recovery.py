"""Stuck-job recovery (SPEC §12): a `running` job whose heartbeat stopped goes back to `queued`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobStage, ProcessingJob, VideoSource
from app.services import jobs

STALE_AFTER = timedelta(seconds=120)  # workers beat every 5 s


async def requeue_stuck_jobs(session: AsyncSession, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    rows = list(
        (
            await session.execute(
                select(ProcessingJob)
                .where(
                    ProcessingJob.status == "running",
                    ProcessingJob.heartbeat_at < now - STALE_AFTER,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for job in rows:
        await session.execute(
            update(JobStage)
            .where(JobStage.job_id == job.id, JobStage.status == "running")
            .values(status="pending", message="Recovered after a worker stopped responding")
        )
        job.status, job.heartbeat_at = "queued", now
        src = await session.get(VideoSource, job.source_id) if job.source_id else None
        await session.commit()
        if src:
            jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), src.kind)
    return len(rows)
