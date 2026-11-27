from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbDep
from app.services import dashboard

router = APIRouter(tags=["dashboard"])


class RecentClip(BaseModel):
    id: str
    title: str
    source_id: str
    updated_at: str


class DashboardOut(BaseModel):
    clips: int
    clips_rendered: int
    renders_failed_qc: int  # finished renders whose quality checks failed
    jobs_failed: int
    recent_clips: list[RecentClip]
    projects: int
    sources: int
    sources_ready: int
    sources_failed: int
    jobs_running: int
    storage_bytes: int


@router.get("/dashboard", response_model=DashboardOut)
async def get(user: CurrentUser, session: DbDep) -> DashboardOut:
    return DashboardOut(**await dashboard.totals(session, user))
