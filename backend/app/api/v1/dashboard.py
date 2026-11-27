from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbDep
from app.services import dashboard

router = APIRouter(tags=["dashboard"])


class DashboardOut(BaseModel):
    projects: int
    sources: int
    sources_ready: int
    sources_failed: int
    jobs_running: int
    storage_bytes: int


@router.get("/dashboard", response_model=DashboardOut)
async def get(user: CurrentUser, session: DbDep) -> DashboardOut:
    return DashboardOut(**await dashboard.totals(session, user))
