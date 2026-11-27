from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api.deps import DbDep, SettingsDep, client_ip
from app.api.v1.auth import auth_response
from app.schemas.auth import AuthOut, SetupAdminIn, SetupStatus
from app.services import auth as auth_service

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatus)
async def status(session: DbDep) -> SetupStatus:
    return SetupStatus(setup_required=await auth_service.setup_required(session))


@router.post("/admin", response_model=AuthOut, status_code=201)
async def create_admin(
    body: SetupAdminIn, request: Request, response: Response, session: DbDep, settings: SettingsDep
) -> AuthOut:
    tokens = await auth_service.create_first_admin(
        session,
        settings,
        email=body.email,
        display_name=body.display_name,
        password=body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return auth_response(tokens, response, settings)
