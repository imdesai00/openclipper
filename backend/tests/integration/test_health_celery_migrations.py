from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.db.models import UserSession
from tests.conftest import DB_URL, PG_ADMIN, _alembic, _recreate_db, make_client

pytestmark = pytest.mark.integration
API = "/api/v1"


async def test_health_live_and_ready(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/health/live")).json() == {"status": "ok"}
    r = await client.get(f"{API}/health/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"] == {"database": "ok", "migrations": "ok", "redis": "ok"}


async def test_ready_reports_unavailable_redis(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = await app_factory(REDIS_URL="redis://localhost:1/0")
    async with make_client(app) as c:
        r = await c.get(f"{API}/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["redis"].startswith("error")
    assert r.json()["checks"]["database"] == "ok"


async def test_ready_reports_unmigrated_db(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = await app_factory()
    async with app.state.engine.begin() as conn:
        real = (await conn.execute(text("select version_num from alembic_version"))).scalar_one()
        await conn.execute(text("update alembic_version set version_num='0000'"))
    try:
        async with make_client(app) as c:
            r = await c.get(f"{API}/health/ready")
        assert r.status_code == 503 and r.json()["checks"]["migrations"].startswith("behind")
    finally:
        async with app.state.engine.begin() as conn:
            await conn.execute(text("update alembic_version set version_num=:v"), {"v": real})


async def test_migrations_down_and_up_on_scratch_db() -> None:
    name = "openclipper_migcheck"
    await _recreate_db(name)
    url = DB_URL.rsplit("/", 1)[0] + f"/{name}"
    _alembic("upgrade", "head", url=url)
    _alembic("downgrade", "base", url=url)
    _alembic("upgrade", "head", url=url)
    import asyncpg

    conn = await asyncpg.connect(PG_ADMIN.rsplit("/", 1)[0] + f"/{name}")
    try:
        tables = {r["tablename"] for r in await conn.fetch("select tablename from pg_tables where schemaname='public'")}
    finally:
        await conn.close()
    assert {
        "users",
        "user_sessions",
        "invitations",
        "audit_logs",
        "settings",
        "alembic_version",
    } <= tables


async def test_fk_columns_are_indexed(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = await app_factory()
    q = text("""
      select c.conrelid::regclass::text as tbl, a.attname as col
      from pg_constraint c join pg_attribute a on a.attrelid=c.conrelid and a.attnum=c.conkey[1]
      where c.contype='f' and not exists (
        select 1 from pg_index i where i.indrelid=c.conrelid and i.indkey[0]=c.conkey[1])
    """)
    async with app.state.engine.connect() as conn:
        missing = (await conn.execute(q)).all()
    assert missing == [], f"unindexed foreign keys: {missing}"


@pytest.fixture
def celery_worker_env(media_worker):  # type: ignore[no-untyped-def]
    return media_worker


async def test_celery_task_runs_on_real_worker(celery_worker_env) -> None:  # type: ignore[no-untyped-def]
    from app.tasks.system import ping

    assert ping.apply_async(queue="default").get(timeout=20) == "pong"


async def test_purge_task_deletes_only_old_sessions(client: AsyncClient, admin, app, celery_worker_env) -> None:  # type: ignore[no-untyped-def]
    from app.tasks.system import purge_expired_sessions

    async with app.state.sessionmaker() as s:
        live = (await s.execute(select(UserSession))).scalars().one()
        s.add(
            UserSession(
                user_id=live.user_id,
                family_id=live.family_id,
                token_hash="a" * 64,
                expires_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        await s.commit()
    deleted = purge_expired_sessions.apply_async(queue="default").get(timeout=20)
    assert deleted == 1
    async with app.state.sessionmaker() as s:
        assert len((await s.execute(select(UserSession))).scalars().all()) == 1
