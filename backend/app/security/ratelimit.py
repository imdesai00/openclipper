from __future__ import annotations

from redis.asyncio import Redis

_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {c, redis.call('TTL', KEYS[1])}
"""


async def hit(redis: Redis, key: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Fixed-window counter. Returns (allowed, retry_after_seconds)."""
    count, ttl = await redis.eval(_LUA, 1, key, window_s)
    if int(count) > limit:
        return False, max(int(ttl), 1)
    return True, 0
