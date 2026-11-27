from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import CurrentUser, RedisDep
from app.db.models import User
from app.ws.hub import Conn

router = APIRouter(tags=["ws"])
ws_router = APIRouter()  # mounted at /ws (not under /api/v1)
TICKET_TTL = 30


@router.post("/ws/ticket")
async def ticket(user: CurrentUser, redis: RedisDep) -> dict[str, int | str]:
    """One-time, 30-second ticket: browsers cannot set an Authorization header on a WebSocket."""
    t = secrets.token_urlsafe(24)
    await redis.set(f"wsticket:{t}", str(user.id), ex=TICKET_TTL)
    return {"ticket": t, "expires_in": TICKET_TTL}


@ws_router.websocket("/ws/jobs")
async def jobs_socket(ws: WebSocket) -> None:
    app = ws.app
    t = ws.query_params.get("ticket", "")
    uid = await app.state.redis.getdel(f"wsticket:{t}") if t else None
    user = None
    if uid:
        import uuid

        async with app.state.sessionmaker() as s:
            user = await s.get(User, uuid.UUID(uid))
    if user is None or not user.is_active:
        await ws.close(code=4401)
        return
    await ws.accept()
    conn = Conn(user_id=user.id, is_admin=user.role == "admin")
    hub = app.state.hub
    hub.add(conn)

    async def sender() -> None:
        while True:
            await ws.send_text(await conn.queue.get())

    send_task = asyncio.create_task(sender())
    try:
        while True:
            msg = await ws.receive_text()  # also detects disconnects
            if msg == "ping":
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        hub.remove(conn)
