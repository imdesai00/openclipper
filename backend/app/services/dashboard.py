"""Dashboard totals. Every number is a database query scoped to what the user may see (SPEC §6.A)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Clip,
    ClipRender,
    ClipVersion,
    MediaAsset,
    ProcessingJob,
    Project,
    ProjectMember,
    User,
    VideoSource,
)


async def totals(session: AsyncSession, user: User) -> dict[str, Any]:
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

    def finals(*cols: Any) -> Any:
        """Finished final renders the user may see."""
        fq = (
            select(*cols)
            .select_from(ClipRender)
            .join(ClipVersion, ClipVersion.id == ClipRender.clip_version_id)
            .join(Clip, Clip.id == ClipVersion.clip_id)
            .where(ClipRender.kind == "final", ClipRender.status == "done")
        )
        sc = scoped(Clip.project_id)
        return fq if sc is None else fq.where(sc)

    rendered = int((await session.execute(finals(func.count(func.distinct(ClipVersion.clip_id))))).scalar_one())
    failed_qc = int((await session.execute(finals(func.count()).where(ClipRender.qc_status == "fail"))).scalar_one())
    recent_q = select(Clip).order_by(Clip.updated_at.desc()).limit(6)
    if (s := scoped(Clip.project_id)) is not None:
        recent_q = recent_q.where(s)
    recent = [
        {"id": str(c.id), "title": c.title, "source_id": str(c.source_id), "updated_at": c.updated_at.isoformat()}
        for c in (await session.execute(recent_q)).scalars()
    ]
    return {
        "clips": await count(Clip, Clip.project_id),
        "clips_rendered": rendered,
        "renders_failed_qc": failed_qc,
        "jobs_failed": await count(ProcessingJob, ProcessingJob.project_id, ProcessingJob.status == "failed"),
        "recent_clips": recent,
        "projects": await count(Project, Project.id),
        "sources": await count(VideoSource, VideoSource.project_id),
        "sources_ready": await count(VideoSource, VideoSource.project_id, VideoSource.status == "ready"),
        "sources_failed": await count(VideoSource, VideoSource.project_id, VideoSource.status == "failed"),
        "jobs_running": await count(
            ProcessingJob, ProcessingJob.project_id, ProcessingJob.status.in_(("queued", "running"))
        ),
        "storage_bytes": int((await session.execute(q)).scalar_one()),
    }
