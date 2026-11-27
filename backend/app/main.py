from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis as SyncRedis
from redis.asyncio import Redis, from_url

from app.api.v1.router import api_router
from app.api.v1.ws import ws_router
from app.config import Settings, get_settings
from app.db.session import make_engine, make_sessionmaker
from app.errors import AppError, install_error_handlers
from app.logging_setup import configure_logging, request_id_var
from app.security import ratelimit
from app.services.storage import build_storage
from app.ws.hub import Hub

log = logging.getLogger("app")
DOC_PATHS = ("/api/docs", "/api/openapi.json", "/api/redoc")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        redis: Redis = from_url(settings.redis_url, decode_responses=True)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.redis = redis
        app.state.sync_redis = SyncRedis.from_url(settings.redis_url)
        app.state.storage = build_storage(settings)
        app.state.hub = Hub(settings.redis_url, app.state.sessionmaker)
        app.state.hub.start()
        log.info("startup complete", extra={"extra_fields": {"env": settings.env}})
        try:
            yield
        finally:
            await app.state.hub.stop()
            app.state.sync_redis.close()
            await redis.aclose()
            await engine.dispose()

    docs = settings.enable_api_docs
    app = FastAPI(
        title="OpenClipper API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    install_error_handlers(app)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["authorization", "content-type", "x-openclipper-client"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid[:64])
        try:
            path = request.url.path
            if not path.startswith("/api/v1/health"):
                ip = request.client.host if request.client else "unknown"
                try:
                    ok, retry = await ratelimit.hit(
                        request.app.state.redis,
                        f"rl:api:{ip}",
                        settings.api_rate_limit_per_minute,
                        60,
                    )
                except Exception:
                    log.exception("rate limiter unavailable")  # visible, not silent; request proceeds
                    ok, retry = True, 0
                if not ok:
                    err = AppError(
                        "rate_limited",
                        "Too many requests",
                        429,
                        headers={"Retry-After": str(retry)},
                    )
                    from app.errors import _envelope

                    resp = _envelope(err.code, err.message, None, 429, err.headers)
                    resp.headers["X-Request-ID"] = rid
                    return resp
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            if not path.startswith(DOC_PATHS):
                response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
            return response
        finally:
            request_id_var.reset(token)

    app.include_router(api_router)
    app.include_router(ws_router)
    return app
