from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import DbDep, SettingsDep, client_ip, require_role
from app.db.models import User
from app.errors import AppError
from app.schemas.auth import InviteIn, InviteOut, UserOut, UserUpdateIn
from app.schemas.common import Page
from app.services import users as users_service

router = APIRouter(prefix="/users", tags=["users"])
AdminDep = Annotated[User, Depends(require_role("admin"))]


@router.post("/invite", response_model=InviteOut, status_code=201)
async def invite(body: InviteIn, request: Request, admin: AdminDep, session: DbDep, settings: SettingsDep) -> InviteOut:
    raw, inv = await users_service.invite(
        session, settings, actor=admin, email=body.email, role=body.role, ip=client_ip(request)
    )
    return InviteOut(email=inv.email, role=inv.role, invite_token=raw, expires_at=inv.expires_at)


@router.get("", response_model=Page[UserOut])
async def list_users(
    _: AdminDep,
    session: DbDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[UserOut]:
    try:
        cur = uuid.UUID(cursor) if cursor else None
    except ValueError as exc:
        raise AppError("invalid_cursor", "Invalid cursor", 400) from exc
    rows, nxt = await users_service.list_users(session, cursor=cur, limit=limit)
    return Page(items=[UserOut.model_validate(r) for r in rows], next_cursor=str(nxt) if nxt else None)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdateIn, request: Request, admin: AdminDep, session: DbDep
) -> UserOut:
    user = await users_service.update_user(
        session,
        actor=admin,
        user_id=user_id,
        role=body.role,
        is_active=body.is_active,
        ip=client_ip(request),
    )
    return UserOut.model_validate(user)
