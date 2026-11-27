"""WebSocket hub: one Redis pattern subscription per API process, fanned out to connected sockets,
filtered per user by project access (SPEC §8)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import events
from app.services.access import project_role

log = logging.getLogger(__name__)
ACCESS_TTL_S = 30.0


@dataclass(eq=False)
class Conn:
    user_id: uuid.UUID
    is_admin: bool
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=500))


class Hub:
    def __init__(self, redis_url: str, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._url = redis_url
        self._sm = sessionmaker
        self._conns: set[Conn] = set()
        self._cache: dict[tuple[uuid.UUID, str], tuple[bool, float]] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="ws-hub")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("websocket hub stopped with an error")

    def add(self, conn: Conn) -> None:
        self._conns.add(conn)

    def remove(self, conn: Conn) -> None:
        self._conns.discard(conn)

    async def _allowed(self, conn: Conn, project_id: str) -> bool:
        if conn.is_admin:
            return True
        key = (conn.user_id, project_id)
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and hit[1] > now:
            return hit[0]
        from app.db.models import User

        async with self._sm() as s:
            user = await s.get(User, conn.user_id)
            ok = bool(user and user.is_active and await project_role(s, user, uuid.UUID(project_id)) is not None)
        self._cache[key] = (ok, now + ACCESS_TTL_S)
        return ok

    async def _run(self) -> None:
        while True:  # reconnect forever; a Redis blip must not kill live updates
            r: Redis | None = None
            try:
                r = from_url(self._url, decode_responses=True)
                ps = r.pubsub()
                await ps.psubscribe(events.CHANNEL_PREFIX + "*")
                async for msg in ps.listen():
                    if msg.get("type") != "pmessage":
                        continue
                    project_id = str(msg["channel"]).removeprefix(events.CHANNEL_PREFIX)
                    for conn in list(self._conns):
                        try:
                            if await self._allowed(conn, project_id):
                                conn.queue.put_nowait(str(msg["data"]))
                        except asyncio.QueueFull:
                            pass  # slow client: drop (progress is re-sent; REST has the truth)
                        except ValueError:
                            continue
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("websocket hub lost its Redis subscription; retrying")
                await asyncio.sleep(2)
            finally:
                if r is not None:
                    await r.aclose()
