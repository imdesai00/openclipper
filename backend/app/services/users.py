from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Invitation, User
from app.errors import AppError, conflict, not_found
from app.security.tokens import new_opaque_token
from app.services import audit
from app.services.auth import normalize_email, revoke_all_for_user


async def invite(
    session: AsyncSession, settings: Settings, *, actor: User, email: str, role: str, ip: str | None
) -> tuple[str, Invitation]:
    email = normalize_email(email)
    if (await session.execute(select(User.id).where(User.email == email))).first():
        raise conflict("email_taken", "An account with this email already exists")
    raw, hashed = new_opaque_token()
    inv = Invitation(
        email=email,
        role=role,
        token_hash=hashed,
        invited_by=actor.id,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.invite_ttl_seconds),
    )
    session.add(inv)
    audit.record(
        session,
        "user.invited",
        actor_id=actor.id,
        target_type="invitation",
        details={"email": email, "role": role},
        ip=ip,
    )
    await session.commit()
    return raw, inv


async def list_users(
    session: AsyncSession, *, cursor: uuid.UUID | None, limit: int
) -> tuple[list[User], uuid.UUID | None]:
    q = select(User).order_by(User.id).limit(limit + 1)
    if cursor:
        q = q.where(User.id > cursor)
    rows = list((await session.execute(q)).scalars())
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return rows[:limit], next_cursor


async def update_user(
    session: AsyncSession,
    *,
    actor: User,
    user_id: uuid.UUID,
    role: str | None,
    is_active: bool | None,
    ip: str | None,
) -> User:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise not_found("User")
    if user.id == actor.id and (is_active is False or (role and role != "admin")):
        raise AppError("self_lockout", "You cannot demote or deactivate yourself", 400)
    removing_admin = (
        user.role == "admin"
        and user.is_active
        and (is_active is False or (role is not None and role != "admin"))
    )
    if removing_admin:
        admins = (
            await session.execute(
                select(func.count()).select_from(User).where(User.role == "admin", User.is_active)
            )
        ).scalar_one()
        if admins <= 1:
            raise AppError("last_admin", "Cannot remove the last active admin", 400)
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
        if not is_active:
            await revoke_all_for_user(session, user.id)
    audit.record(
        session,
        "user.updated",
        actor_id=actor.id,
        target_type="user",
        target_id=str(user.id),
        details={"role": role, "is_active": is_active},
        ip=ip,
    )
    await session.commit()
    return user
