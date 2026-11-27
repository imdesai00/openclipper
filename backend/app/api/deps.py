from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import User
from app.errors import forbidden, unauthorized
from app.security.tokens import decode_access_token

CSRF_HEADER = "x-openclipper-client"


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis  # type: ignore[no-any-return]


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def current_user(request: Request, session: DbDep, settings: SettingsDep) -> User:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized()
    claims = decode_access_token(token, settings)
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise unauthorized("Account disabled or removed", "account_disabled")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _dep(user: CurrentUser) -> User:
        if user.role not in roles:
            raise forbidden()
        return user

    return _dep


def require_csrf_header(request: Request) -> None:
    """Cookie-authenticated endpoints need a custom header that cross-site forms cannot send."""
    if request.headers.get(CSRF_HEADER) != "web":
        raise forbidden("Missing client header")
