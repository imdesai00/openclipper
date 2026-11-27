from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import (
    CurrentUser,
    DbDep,
    RedisDep,
    SettingsDep,
    client_ip,
    require_csrf_header,
)
from app.config import Settings
from app.errors import AppError, unauthorized
from app.schemas.auth import AcceptInviteIn, AuthOut, LoginIn, UserOut
from app.security import ratelimit
from app.services import auth as auth_service
from app.services.auth import IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE = "oc_refresh"
COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


def auth_response(tokens: IssuedTokens, response: Response, settings: Settings) -> AuthOut:
    set_refresh_cookie(response, tokens.refresh_token, settings)
    response.headers["Cache-Control"] = "no-store"
    return AuthOut(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=UserOut.model_validate(tokens.user),
    )


@router.post("/login", response_model=AuthOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: DbDep,
    settings: SettingsDep,
    redis: RedisDep,
) -> AuthOut:
    ip = client_ip(request) or "unknown"
    email = auth_service.normalize_email(body.email)
    for key, limit in (
        (f"rl:login:ip:{ip}", settings.login_rate_limit_per_ip),
        (f"rl:login:email:{email}", settings.login_rate_limit_per_email),
    ):
        allowed, retry = await ratelimit.hit(redis, key, limit, settings.login_rate_window_seconds)
        if not allowed:
            raise AppError(
                "rate_limited",
                "Too many login attempts. Try again later.",
                429,
                headers={"Retry-After": str(retry)},
            )
    tokens = await auth_service.login(
        session,
        settings,
        email=email,
        password=body.password,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    return auth_response(tokens, response, settings)


@router.post("/refresh", response_model=AuthOut, dependencies=[Depends(require_csrf_header)])
async def refresh(
    request: Request, response: Response, session: DbDep, settings: SettingsDep
) -> AuthOut:
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise unauthorized("No session", "no_session")
    try:
        tokens = await auth_service.refresh(
            session,
            settings,
            raw_token=raw,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AppError:
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        raise
    return auth_response(tokens, response, settings)


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf_header)])
async def logout(request: Request, response: Response, session: DbDep) -> Response:
    await auth_service.logout(session, raw_token=request.cookies.get(COOKIE), ip=client_ip(request))
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/accept-invite", response_model=AuthOut, status_code=201)
async def accept_invite(
    body: AcceptInviteIn,
    request: Request,
    response: Response,
    session: DbDep,
    settings: SettingsDep,
) -> AuthOut:
    tokens = await auth_service.accept_invite(
        session,
        settings,
        token=body.token,
        display_name=body.display_name,
        password=body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return auth_response(tokens, response, settings)
