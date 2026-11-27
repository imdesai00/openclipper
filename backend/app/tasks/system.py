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


async def _purge_uploads() -> int:
    from sqlalchemy.pool import NullPool

    from app.services import uploads

    settings = get_settings()
    engine = make_engine(settings.database_url, poolclass=NullPool)
    try:
        async with make_sessionmaker(engine)() as session:
            return await uploads.purge_expired(session, settings)
    finally:
        await engine.dispose()


@celery_app.task(name="system.purge_expired_uploads")
def purge_expired_uploads() -> int:
    return asyncio.run(_purge_uploads())


async def _recover_stuck() -> int:
    from sqlalchemy.pool import NullPool

    from app.services import recovery

    engine = make_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with make_sessionmaker(engine)() as session:
            return await recovery.requeue_stuck_jobs(session)
    finally:
        await engine.dispose()


@celery_app.task(name="system.recover_stuck_jobs")
def recover_stuck_jobs() -> int:
    return asyncio.run(_recover_stuck())
