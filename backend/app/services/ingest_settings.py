"""Admin switch + daily limit for link downloads (DECISIONS D-010). Off by default."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting, User, VideoSource
from app.services import audit

KEY = "ingest"
HEARTBEAT_KEY = "ingest:heartbeat"
DEFAULT = {"enabled": False, "daily_limit": 10}


@dataclass(frozen=True)
class IngestState:
    enabled: bool
    daily_limit: int
    worker_running: bool

    @property
    def available(self) -> bool:
        return self.enabled and self.worker_running


async def get_state(session: AsyncSession, redis: Redis) -> IngestState:
    row = (
        await session.execute(select(Setting).where(Setting.scope == "global", Setting.key == KEY))
    ).scalar_one_or_none()
    val = {**DEFAULT, **(row.value if row else {})}
    return IngestState(bool(val["enabled"]), int(val["daily_limit"]), bool(await redis.exists(HEARTBEAT_KEY)))


async def set_state(
    session: AsyncSession,
    redis: Redis,
    actor: User,
    enabled: bool,
    daily_limit: int,
    ip: str | None,
) -> IngestState:
    row = (
        await session.execute(select(Setting).where(Setting.scope == "global", Setting.key == KEY).with_for_update())
    ).scalar_one_or_none()
    value = {"enabled": enabled, "daily_limit": daily_limit}
    if row is None:
        session.add(Setting(scope="global", key=KEY, value=value))
    else:
        row.value = value
    audit.record(session, "settings.ingest_updated", actor_id=actor.id, details=value, ip=ip)
    await session.commit()
    return await get_state(session, redis)


async def downloads_today(session: AsyncSession) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(VideoSource)
                .where(VideoSource.kind == "ytdlp", VideoSource.created_at >= start)
            )
        ).scalar_one()
    )
