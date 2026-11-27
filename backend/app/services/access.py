"""Project-level authorization. Every project-scoped service call goes through here (SPEC §6.O)."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, User
from app.errors import forbidden, not_found


class Need(StrEnum):
    READ = "read"
    WRITE = "write"
    OWNER = "owner"


async def project_role(session: AsyncSession, user: User, project_id: uuid.UUID) -> str | None:
    if user.role == "admin":
        return "owner"  # admins can manage every project
    row = (
        await session.execute(
            select(ProjectMember.role).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        )
    ).scalar_one_or_none()
    return row


async def require_project(session: AsyncSession, user: User, project_id: uuid.UUID, need: Need) -> tuple[Project, str]:
    project = await session.get(Project, project_id)
    role = await project_role(session, user, project_id) if project else None
    if project is None or role is None:
        raise not_found("Project")  # same answer for "missing" and "not yours": no existence leak
    if need is Need.READ:
        return project, role
    if user.role == "viewer":
        raise forbidden("Viewers have read-only access")
    if need is Need.WRITE and role in ("owner", "editor"):
        return project, role
    if need is Need.OWNER and role == "owner":
        return project, role
    raise forbidden()


async def can_read_project(session: AsyncSession, user: User, project_id: uuid.UUID) -> bool:
    return await project_role(session, user, project_id) is not None
