from __future__ import annotations

import base64
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
PG_ADMIN = os.environ.get("TEST_PG_ADMIN_DSN", "postgresql://postgres:dev@localhost:55432/postgres")
TEST_DB = "openclipper_test"
DB_URL = os.environ.get(
    "TEST_DATABASE_URL", f"postgresql+asyncpg://postgres:dev@localhost:55432/{TEST_DB}"
)
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")
ENC_KEY = base64.b64encode(b"k" * 32).decode()
JWT_SECRET = "j" * 48

# Celery reads settings at import time.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": DB_URL,
        "REDIS_URL": REDIS_URL,
        "APP_ENCRYPTION_KEY": ENC_KEY,
        "APP_JWT_SECRET": JWT_SECRET,
    }
)

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "APP_ENV": "test",
        "DATABASE_URL": DB_URL,
        "REDIS_URL": REDIS_URL,
        "APP_ENCRYPTION_KEY": SecretStr(ENC_KEY),
        "APP_JWT_SECRET": SecretStr(JWT_SECRET),
        "COOKIE_SECURE": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _alembic(*args: str, url: str = DB_URL) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        check=True,
        env={**os.environ, "DATABASE_URL": url},
    )


async def _recreate_db(name: str) -> None:
    conn = await asyncpg.connect(PG_ADMIN)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session")
async def _database() -> AsyncIterator[None]:
    if os.environ.get("SKIP_DB_SETUP"):
        yield
        return
    try:
        await _recreate_db(TEST_DB)
    except OSError as exc:  # pragma: no cover
        pytest.skip(f"Postgres not reachable: {exc}", allow_module_level=True)
    _alembic("upgrade", "head")
    yield


@pytest_asyncio.fixture
async def app_factory(_database):  # type: ignore[no-untyped-def]
    """Build a running app (lifespan entered) with optional settings overrides."""
    started = []

    async def _make(**overrides: object):  # type: ignore[no-untyped-def]
        app = create_app(make_settings(**overrides))
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        started.append(ctx)
        from redis.asyncio import from_url

        r = from_url(REDIS_URL)
        await r.flushdb()
        await r.aclose()
        async with app.state.engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(
                text("TRUNCATE audit_logs, invitations, user_sessions, settings, users CASCADE")
            )
        return app

    yield _make
    for ctx in started:
        await ctx.__aexit__(None, None, None)


@pytest_asyncio.fixture
async def app(app_factory):  # type: ignore[no-untyped-def]
    return await app_factory()


def make_client(app) -> AsyncClient:  # type: ignore[no-untyped-def]
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    async with make_client(app) as c:
        yield c


ADMIN = {"email": "admin@example.com", "display_name": "Admin", "password": "correct horse battery"}
WEB = {"X-OpenClipper-Client": "web"}


@pytest_asyncio.fixture
async def admin(client: AsyncClient) -> dict[str, str]:
    r = await client.post("/api/v1/setup/admin", json=ADMIN)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
