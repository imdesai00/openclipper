from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db.session import make_engine, make_sessionmaker
from app.services import auth as auth_service
from app.tasks.celery_app import celery_app


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"


async def _purge() -> int:
    from sqlalchemy.pool import NullPool

    engine = make_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with make_sessionmaker(engine)() as session:
            return await auth_service.purge_expired_sessions(session)
    finally:
        await engine.dispose()


@celery_app.task(name="system.purge_expired_sessions")
def purge_expired_sessions() -> int:
    return asyncio.run(_purge())
