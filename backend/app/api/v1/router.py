from fastapi import APIRouter

from app.api.v1 import ai, auth, dashboard, features, health, media, projects, setup, sources, uploads, users, ws

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
api_router.include_router(ai.router)
api_router.include_router(ws.router)
