"""Dashboard totals. Every number is a database query scoped to what the user may see (SPEC §6.A)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MediaAsset, ProcessingJob, Project, ProjectMember, User, VideoSource


async def totals(session: AsyncSession, user: User) -> dict[str, int]:
    def scoped(col: Any) -> Any:
        if user.role == "admin":
            return None
        return col.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id))

    async def count(model: Any, col: Any, *where: Any) -> int:
        q = select(func.count()).select_from(model)
        if (s := scoped(col)) is not None:
            q = q.where(s)
        for w in where:
            q = q.where(w)
        return int((await session.execute(q)).scalar_one())

    q = select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(MediaAsset.deleted_at.is_(None))
    if (s := scoped(MediaAsset.project_id)) is not None:
        q = q.where(s)
    return {
        "projects": await count(Project, Project.id),
        "sources": await count(VideoSource, VideoSource.project_id),
        "sources_ready": await count(VideoSource, VideoSource.project_id, VideoSource.status == "ready"),
        "sources_failed": await count(VideoSource, VideoSource.project_id, VideoSource.status == "failed"),
        "jobs_running": await count(
            ProcessingJob, ProcessingJob.project_id, ProcessingJob.status.in_(("queued", "running"))
        ),
        "storage_bytes": int((await session.execute(q)).scalar_one()),
    }
