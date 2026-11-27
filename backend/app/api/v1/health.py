from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DbDep, RedisDep

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


def _head_revision() -> str | None:
    cfg = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


@router.get("/ready")
async def ready(session: DbDep, redis: RedisDep) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        await session.execute(text("select 1"))
        current = (await session.execute(text("select version_num from alembic_version"))).scalar()
        head = _head_revision()
        checks["database"] = "ok"
        checks["migrations"] = "ok" if current == head else f"behind (db={current}, head={head})"
    except Exception as exc:
        checks.setdefault("database", f"error: {type(exc).__name__}")
        checks.setdefault("migrations", "unknown")
    try:
        await asyncio.wait_for(redis.ping(), timeout=2)
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status_code=200 if healthy else 503,
    )
