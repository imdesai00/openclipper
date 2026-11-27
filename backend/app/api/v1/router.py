from fastapi import APIRouter

from app.api.v1 import (
    ai,
    auth,
    clips,
    dashboard,
    features,
    health,
    highlights,
    media,
    projects,
    setup,
    sources,
    transcripts,
    uploads,
    users,
    ws,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(setup.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(features.router)
api_router.include_router(dashboard.router)
api_router.include_router(projects.router)
api_router.include_router(sources.router)
api_router.include_router(uploads.router)
api_router.include_router(media.router)
api_router.include_router(transcripts.router)
api_router.include_router(highlights.router)
api_router.include_router(clips.router)
api_router.include_router(ai.router)
api_router.include_router(ws.router)
