"""Authentication use-cases. Used by REST now and by MCP/Celery later."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Invitation, User, UserSession
from app.errors import AppError, conflict, unauthorized
from app.security.passwords import DUMMY_HASH, hash_password, needs_rehash, verify_password
from app.security.tokens import create_access_token, hash_token, new_opaque_token
from app.services import audit

_SETUP_LOCK_ID = 727_001


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def setup_required(session: AsyncSession) -> bool:
    return (await session.execute(select(User.id).limit(1))).first() is None


async def _issue(
    session: AsyncSession,
    user: User,
    settings: Settings,
    *,
    family_id: uuid.UUID | None = None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[IssuedTokens, UserSession]:
    raw, hashed = new_opaque_token()
    row = UserSession(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hashed,
        expires_at=_now() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
    )
    session.add(row)
    await session.flush()
    access = create_access_token(user.id, user.role, settings)
    return IssuedTokens(access, raw, settings.access_token_ttl_seconds, user), row


async def create_first_admin(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    display_name: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> IssuedTokens:
    # Serialize concurrent setup attempts; only the first can win.
    await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _SETUP_LOCK_ID})
    if not await setup_required(session):
        raise conflict("setup_complete", "Setup has already been completed")
    user = User(
        email=normalize_email(email),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role="admin",
        last_login_at=_now(),
    )
    session.add(user)
    await session.flush()
    tokens, _ = await _issue(session, user, settings, ip=ip, user_agent=user_agent)
    audit.record(
        session,
        "setup.admin_created",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip=ip,
    )
    await session.commit()
    return tokens


async def login(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> IssuedTokens:
    email = normalize_email(email)
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # Always run one verification so timing doesn't reveal whether the email exists.
    ok = verify_password(user.password_hash if user else DUMMY_HASH, password)
    if user is None or not ok or not user.is_active:
        audit.record(session, "auth.login_failed", actor_kind="anonymous", details={"email": email}, ip=ip)
        await session.commit()
        raise unauthorized("Invalid email or password", "invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = _now()
    tokens, _ = await _issue(session, user, settings, ip=ip, user_agent=user_agent)
    audit.record(session, "auth.login", actor_id=user.id, ip=ip)
    await session.commit()
    return tokens


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.family_id == family_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def refresh(
    session: AsyncSession,
    settings: Settings,
    *,
    raw_token: str,
    ip: str | None,
    user_agent: str | None,
) -> IssuedTokens:
    row = (
        await session.execute(
            select(UserSession).where(UserSession.token_hash == hash_token(raw_token)).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise unauthorized("Invalid session", "invalid_session")

    if row.revoked_at is not None:
        if row.replaced_by is not None:
            # A rotated token was presented again: assume theft, kill the whole family.
            await _revoke_family(session, row.family_id)
            audit.record(
                session,
                "auth.refresh_reuse_detected",
                actor_id=row.user_id,
                details={"family_id": str(row.family_id)},
                ip=ip,
            )
            await session.commit()
            raise unauthorized("Session revoked", "refresh_reuse_detected")
        raise unauthorized("Invalid session", "invalid_session")

    user = await session.get(User, row.user_id)
    if row.expires_at <= _now() or user is None or not user.is_active:
        await _revoke_family(session, row.family_id)
        await session.commit()
        raise unauthorized("Session expired", "session_expired")

    tokens, new_row = await _issue(session, user, settings, family_id=row.family_id, ip=ip, user_agent=user_agent)
    row.revoked_at = _now()
    row.replaced_by = new_row.id
    await session.commit()
    return tokens


async def logout(session: AsyncSession, *, raw_token: str | None, ip: str | None) -> None:
    if not raw_token:
        return
    row = (
        await session.execute(select(UserSession).where(UserSession.token_hash == hash_token(raw_token)))
    ).scalar_one_or_none()
    if row is None:
        return
    await _revoke_family(session, row.family_id)
    audit.record(session, "auth.logout", actor_id=row.user_id, ip=ip)
    await session.commit()


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def accept_invite(
    session: AsyncSession,
    settings: Settings,
    *,
    token: str,
    display_name: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> IssuedTokens:
    inv = (
        await session.execute(select(Invitation).where(Invitation.token_hash == hash_token(token)).with_for_update())
    ).scalar_one_or_none()
    if inv is None or inv.accepted_at is not None or inv.expires_at <= _now():
        raise AppError("invalid_invite", "Invitation is invalid or has expired", 400)
    existing = (await session.execute(select(User.id).where(User.email == inv.email))).first()
    if existing:
        raise conflict("email_taken", "An account with this email already exists")
    user = User(
        email=inv.email,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=inv.role,
        last_login_at=_now(),
    )
    session.add(user)
    await session.flush()
    inv.accepted_at = _now()
    tokens, _ = await _issue(session, user, settings, ip=ip, user_agent=user_agent)
    audit.record(
        session,
        "user.invite_accepted",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip=ip,
    )
    await session.commit()
    return tokens


async def purge_expired_sessions(session: AsyncSession, *, older_than_days: int = 7) -> int:
    cutoff = _now() - timedelta(days=older_than_days)
    res = await session.execute(delete(UserSession).where(UserSession.expires_at < cutoff))
    await session.commit()
    return int(res.rowcount or 0)  # type: ignore[attr-defined]
