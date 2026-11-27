from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.adapters.sources.direct_url import ALLOWED_EXT
from app.api.deps import CurrentUser, DbDep, RedisDep, SettingsDep, client_ip, require_role
from app.db.models import User
from app.schemas.media import FeaturesOut, IngestSettingsIn
from app.services import ingest_settings

router = APIRouter(tags=["features"])
AdminDep = Annotated[User, Depends(require_role("admin"))]


async def _features(session: DbDep, redis: RedisDep, settings: SettingsDep) -> FeaturesOut:
    st = await ingest_settings.get_state(session, redis)
    return FeaturesOut(
        ingest_enabled=st.enabled,
        ingest_available=st.available,
        ingest_daily_limit=st.daily_limit,
        max_upload_bytes=settings.max_upload_bytes,
        allowed_extensions=sorted(ALLOWED_EXT),
    )


@router.get("/features", response_model=FeaturesOut)
async def features(_: CurrentUser, session: DbDep, redis: RedisDep, settings: SettingsDep) -> FeaturesOut:
    return await _features(session, redis, settings)


@router.put("/settings/ingest", response_model=FeaturesOut)
async def set_ingest(
    body: IngestSettingsIn,
    request: Request,
    admin: AdminDep,
    session: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> FeaturesOut:
    await ingest_settings.set_state(session, redis, admin, body.enabled, body.daily_limit, client_ip(request))
    return await _features(session, redis, settings)
