from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import MediaAsset, Project, ProjectMember, Upload, User, VideoSource
from app.errors import AppError, conflict, forbidden, not_found
from app.ports.storage import StorageBackend
from app.services import audit, jobs
from app.services.access import Need, require_project

log = logging.getLogger(__name__)


async def create_project(
    session: AsyncSession, user: User, name: str, description: str | None, ip: str | None
) -> Project:
    if user.role == "viewer":
        raise forbidden("Viewers cannot create projects")
    project = Project(name=name.strip(), description=description, owner_id=user.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    audit.record(
        session,
        "project.created",
        actor_id=user.id,
        target_type="project",
        target_id=str(project.id),
        ip=ip,
    )
    await session.commit()
    return project


async def list_projects(
    session: AsyncSession, user: User, *, cursor: uuid.UUID | None, limit: int
) -> tuple[list[tuple[Project, str, int]], uuid.UUID | None]:
    counts = select(VideoSource.project_id, func.count().label("n")).group_by(VideoSource.project_id).subquery()
    q = (
        select(Project, ProjectMember.role, func.coalesce(counts.c.n, 0))
        .outerjoin(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == user.id))
        .outerjoin(counts, counts.c.project_id == Project.id)
        .order_by(Project.id)
        .limit(limit + 1)
    )
    if user.role != "admin":
        q = q.where(ProjectMember.user_id == user.id)
    if cursor:
        q = q.where(Project.id > cursor)
    rows = (await session.execute(q)).all()
    nxt = rows[limit - 1][0].id if len(rows) > limit else None
    return [(p, "owner" if user.role == "admin" and role is None else role, int(n)) for p, role, n in rows[:limit]], nxt


async def update_project(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    name: str | None,
    description: str | None,
    ip: str | None,
) -> tuple[Project, str]:
    project, role = await require_project(session, user, project_id, Need.OWNER)
    if name is not None:
        project.name = name.strip()
    if description is not None:
        project.description = description
    audit.record(
        session,
        "project.updated",
        actor_id=user.id,
        target_type="project",
        target_id=str(project.id),
        ip=ip,
    )
    await session.commit()
    return project, role


async def delete_project(
    session: AsyncSession,
    storage: StorageBackend,
    settings: Settings,
    user: User,
    project_id: uuid.UUID,
    confirm_name: str,
    ip: str | None,
) -> None:
    """Permanent delete after an explicit, typed confirmation (SPEC §9). Refused while jobs run."""
    project, _ = await require_project(session, user, project_id, Need.OWNER)
    if confirm_name != project.name:
        raise AppError("confirmation_mismatch", "Type the project name exactly to confirm", 400)
    if await jobs.active_job_exists(session, project_id=project_id):
        raise conflict("jobs_running", "Processing is still running for this project. Cancel it first.")
    keys = [
        k
        for (k,) in (
            await session.execute(select(MediaAsset.storage_key).where(MediaAsset.project_id == project_id))
        ).all()
    ]
    staging = [
        n
        for (n,) in (
            await session.execute(
                select(Upload.staging_name).where(Upload.project_id == project_id, Upload.status == "active")
            )
        ).all()
    ]
    audit.record(
        session,
        "project.deleted",
        actor_id=user.id,
        target_type="project",
        target_id=str(project_id),
        details={"name": project.name, "assets": len(keys)},
        ip=ip,
    )
    await session.delete(project)  # cascades to sources, assets rows, jobs, uploads
    await session.commit()
    from pathlib import Path

    for key in keys:
        try:
            storage.delete(key)
        except Exception:  # the DB is already consistent; the orphan sweep (retention milestone) will catch leftovers
            log.exception("could not delete stored object", extra={"extra_fields": {"key": key}})
    for name in staging:
        (Path(settings.upload_staging_dir) / name).unlink(missing_ok=True)


async def list_members(session: AsyncSession, user: User, project_id: uuid.UUID) -> list[tuple[ProjectMember, User]]:
    await require_project(session, user, project_id, Need.READ)
    rows = (
        await session.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.id)
        )
    ).all()
    return [(m, u) for m, u in rows]


async def add_member(
    session: AsyncSession, user: User, project_id: uuid.UUID, email: str, role: str, ip: str | None
) -> tuple[ProjectMember, User]:
    await require_project(session, user, project_id, Need.OWNER)
    target = (
        await session.execute(select(User).where(User.email == email.strip().lower(), User.is_active))
    ).scalar_one_or_none()
    if target is None:
        raise not_found("User")
    existing = (
        await session.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == target.id)
        )
    ).scalar_one_or_none()
    if existing:
        raise conflict("already_member", "That user is already a member")
    m = ProjectMember(project_id=project_id, user_id=target.id, role=role)
    session.add(m)
    audit.record(
        session,
        "project.member_added",
        actor_id=user.id,
        target_type="project",
        target_id=str(project_id),
        details={"user_id": str(target.id), "role": role},
        ip=ip,
    )
    await session.commit()
    return m, target


async def remove_member(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    member_user_id: uuid.UUID,
    ip: str | None,
) -> None:
    project, _ = await require_project(session, user, project_id, Need.OWNER)
    if member_user_id == project.owner_id:
        raise AppError("owner_required", "The project owner cannot be removed", 400)
    m = (
        await session.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == member_user_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise not_found("Member")
    await session.delete(m)
    audit.record(
        session,
        "project.member_removed",
        actor_id=user.id,
        target_type="project",
        target_id=str(project_id),
        details={"user_id": str(member_user_id)},
        ip=ip,
    )
    await session.commit()
