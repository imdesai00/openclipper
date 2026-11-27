"""Progress events: workers publish to Redis, the API's WebSocket hub fans them out (SPEC §4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from redis import Redis as SyncRedis
from redis.asyncio import Redis

CHANNEL_PREFIX = "jobs:project:"


def channel(project_id: Any) -> str:
    return f"{CHANNEL_PREFIX}{project_id}"


def _body(payload: dict[str, Any]) -> str:
    return json.dumps({**payload, "ts": datetime.now(UTC).isoformat()}, default=str)


async def publish(redis: Redis, project_id: Any, payload: dict[str, Any]) -> None:
    await redis.publish(channel(project_id), _body(payload))


def publish_sync(redis: SyncRedis, project_id: Any, payload: dict[str, Any]) -> None:
    redis.publish(channel(project_id), _body(payload))
