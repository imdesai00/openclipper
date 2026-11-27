from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, RedisDep, SettingsDep, StorageDep, client_ip
from app.api.presenters import source_out
from app.db.models import VideoSource
from app.errors import AppError
from app.schemas.common import Page
from app.schemas.media import (
    LinkSourceIn,
    MemberIn,
    MemberOut,
    ProjectDeleteIn,
    ProjectIn,
    ProjectOut,
    ProjectUpdateIn,
    SourceOut,
)
from app.services import projects as svc
from app.services import sources as sources_svc
from app.services.access import Need, require_project

router = APIRouter(prefix="/projects", tags=["projects"])
Limit = Annotated[int, Query(ge=1, le=100)]


def _cursor(raw: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError as exc:
        raise AppError("invalid_cursor", "Invalid cursor", 400) from exc


@router.post("", response_model=ProjectOut, status_code=201)
async def create(body: ProjectIn, request: Request, user: CurrentUser, session: DbDep) -> ProjectOut:
    p = await svc.create_project(session, user, body.name, body.description, client_ip(request))
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description,
        owner_id=p.owner_id,
        created_at=p.created_at,
        my_role="owner",
    )


@router.get("", response_model=Page[ProjectOut])
async def list_(user: CurrentUser, session: DbDep, cursor: str | None = None, limit: Limit = 50) -> Page[ProjectOut]:
    rows, nxt = await svc.list_projects(session, user, cursor=_cursor(cursor), limit=limit)
    items = [
        ProjectOut(
            id=p.id,
            name=p.name,
            description=p.description,
            owner_id=p.owner_id,
            created_at=p.created_at,
            my_role=role,
            source_count=n,
        )
        for p, role, n in rows
    ]
    return Page(items=items, next_cursor=str(nxt) if nxt else None)


@router.get("/{project_id}", response_model=ProjectOut)
async def get(project_id: uuid.UUID, user: CurrentUser, session: DbDep) -> ProjectOut:
    p, role = await require_project(session, user, project_id, Need.READ)
    n = (
        await session.execute(select(func.count()).select_from(VideoSource).where(VideoSource.project_id == p.id))
    ).scalar_one()
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description,
        owner_id=p.owner_id,
        created_at=p.created_at,
        my_role=role,
        source_count=n,
    )


@router.patch("/{project_id}", response_model=ProjectOut)
async def update(
    project_id: uuid.UUID,
    body: ProjectUpdateIn,
    request: Request,
    user: CurrentUser,
    session: DbDep,
) -> ProjectOut:
    p, role = await svc.update_project(session, user, project_id, body.name, body.description, client_ip(request))
    return ProjectOut(
        id=p.id, name=p.name, description=p.description, owner_id=p.owner_id, created_at=p.created_at, my_role=role
    )


@router.delete("/{project_id}", status_code=204)
async def delete(
    project_id: uuid.UUID,
    body: ProjectDeleteIn,
    request: Request,
    user: CurrentUser,
    session: DbDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    await svc.delete_project(session, storage, settings, user, project_id, body.confirm_name, client_ip(request))
    return Response(status_code=204)


@router.get("/{project_id}/members", response_model=list[MemberOut])
async def members(project_id: uuid.UUID, user: CurrentUser, session: DbDep) -> list[MemberOut]:
    return [
        MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role)
        for m, u in await svc.list_members(session, user, project_id)
    ]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    project_id: uuid.UUID, body: MemberIn, request: Request, user: CurrentUser, session: DbDep
) -> MemberOut:
    m, u = await svc.add_member(session, user, project_id, body.email, body.role, client_ip(request))
    return MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role)


@router.delete("/{project_id}/members/{member_id}", status_code=204)
async def remove_member(
    project_id: uuid.UUID, member_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep
) -> Response:
    await svc.remove_member(session, user, project_id, member_id, client_ip(request))
    return Response(status_code=204)


@router.get("/{project_id}/sources", response_model=Page[SourceOut])
async def list_sources(
    project_id: uuid.UUID,
    user: CurrentUser,
    session: DbDep,
    settings: SettingsDep,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[SourceOut]:
    rows, nxt = await sources_svc.list_sources(session, user, project_id, cursor=_cursor(cursor), limit=limit)
    return Page(items=[source_out(s, settings) for s in rows], next_cursor=str(nxt) if nxt else None)


@router.post("/{project_id}/sources/link", response_model=SourceOut, status_code=202)
async def add_link(
    project_id: uuid.UUID,
    body: LinkSourceIn,
    request: Request,
    user: CurrentUser,
    session: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> SourceOut:
    src, _ = await sources_svc.create_from_link(
        session, redis, settings, user, project_id, body.url, body.attested, client_ip(request)
    )
    return source_out(src, settings)
