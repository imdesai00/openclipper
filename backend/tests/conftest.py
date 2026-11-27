from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
import sys
import tempfile
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
DB_URL = os.environ.get("TEST_DATABASE_URL", f"postgresql+asyncpg://postgres:dev@localhost:55432/{TEST_DB}")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")
ENC_KEY = base64.b64encode(b"k" * 32).decode()
JWT_SECRET = "j" * 48

_WORK = Path(tempfile.mkdtemp(prefix="oc-test-"))
# Celery reads settings at import time.
os.environ.update(
    {
        "STORAGE_LOCAL_ROOT": str(_WORK / "storage"),
        "UPLOAD_STAGING_DIR": str(_WORK / "staging"),
        "SCRATCH_DIR": str(_WORK / "scratch"),
        "DIRECT_URL_ALLOW_PRIVATE": "false",
        "APP_ENV": "test",
        "DATABASE_URL": DB_URL,
        "REDIS_URL": REDIS_URL,
        "APP_ENCRYPTION_KEY": ENC_KEY,
        "APP_JWT_SECRET": JWT_SECRET,
        # Real speech recognition in tests: the smallest real model, cached outside the temp dir so it downloads once.
        "WHISPER_MODEL": "tiny",
        "WHISPER_CACHE_DIR": os.environ.get("TEST_WHISPER_CACHE", str(Path.home() / ".cache/openclipper-test-models")),
        "WHISPER_DEVICE": "cpu",
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
        "API_RATE_LIMIT_PER_MINUTE": 1_000_000,
        "STORAGE_LOCAL_ROOT": str(_WORK / "storage"),
        "UPLOAD_STAGING_DIR": str(_WORK / "staging"),
        "SCRATCH_DIR": str(_WORK / "scratch"),
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


async def reset_state() -> None:
    """Empty every table, Redis and the storage directories so each test starts clean."""
    import asyncpg
    from redis.asyncio import from_url

    r = from_url(REDIS_URL)
    await r.flushdb()
    await r.aclose()
    conn = await asyncpg.connect(DB_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        names = [
            x["tablename"]
            for x in await conn.fetch(
                "select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'"
            )
        ]
        await conn.execute("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " CASCADE")
    finally:
        await conn.close()
    for sub in ("storage", "staging", "scratch"):
        shutil.rmtree(_WORK / sub, ignore_errors=True)


@pytest_asyncio.fixture
async def app_factory(_database):  # type: ignore[no-untyped-def]
    """Build a running app (lifespan entered) with optional settings overrides."""
    started = []

    async def _make(**overrides: object):  # type: ignore[no-untyped-def]
        await reset_state()
        app = create_app(make_settings(**overrides))
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        started.append(ctx)
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


# ---------------------------------------------------------------- media fixtures (generated, never downloaded)
def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("media")
    lav = ["-f", "lavfi", "-i"]
    # 3 s, 320x240, tone audio
    _ffmpeg(*lav, "testsrc2=size=320x240:rate=25:duration=3", *lav, "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "small.mp4"))  # fmt: skip
    # 2 s 1080p without audio (proxy must downscale to 720 and waveform must report no audio)
    _ffmpeg(*lav, "testsrc2=size=1920x1080:rate=25:duration=2", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-an", str(d / "hd_silent.mp4"))  # fmt: skip
    # 30 s 1080p: slow to encode with a slow preset (cancellation test)
    _ffmpeg(*lav, "testsrc2=size=1920x1080:rate=30:duration=30", *lav, "sine=frequency=300:duration=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(d / "long_hd.mp4"))  # fmt: skip
    # audio only
    _ffmpeg(*lav, "sine=frequency=500:duration=3", "-c:a", "aac", str(d / "audio_only.mp4"))
    (d / "not_video.mp4").write_text("this is just text, not a video\n" * 50)
    return d


@pytest.fixture(scope="session")
def small_mp4(media_dir: Path) -> Path:
    return media_dir / "small.mp4"


# ---------------------------------------------------------------- real worker + real HTTP server
@pytest.fixture(scope="session")
def media_worker(_database):  # type: ignore[no-untyped-def]
    """A real Celery worker (Redis broker). It also consumes `asr`: a second in-process test worker shares global shutdown
    state in Celery and cannot be stopped cleanly, and the ASR tests are the only ones that route work to that queue."""
    from celery.contrib.testing.worker import start_worker

    import app.tasks.asr  # noqa: F401  the worker only learns about tasks registered before it starts
    from app.tasks.celery_app import celery_app

    celery_app.conf.update(broker_url=REDIS_URL, result_backend=REDIS_URL)
    with start_worker(celery_app, perform_ping_check=False, pool="solo", queues=["default", "media", "asr"],
                      loglevel="WARNING", shutdown_timeout=30):  # fmt: skip
        yield celery_app


class Live:
    def __init__(self, app, url: str) -> None:  # type: ignore[no-untyped-def]
        self.app, self.url = app, url


@pytest_asyncio.fixture
async def live_factory(_database):  # type: ignore[no-untyped-def]
    """Start the API on a real TCP port (uvicorn), optionally with settings overrides."""
    import uvicorn

    running = []

    async def _start(**overrides: object) -> Live:
        await reset_state()
        app = create_app(make_settings(COOKIE_SECURE=False, **overrides))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
        task = asyncio.create_task(server.serve())
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn did not start"
        running.append((server, task))
        return Live(app, f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}")

    yield _start
    for server, task in running:
        server.should_exit = True
        await task


@pytest_asyncio.fixture
async def live(live_factory):  # type: ignore[no-untyped-def]
    return await live_factory()
