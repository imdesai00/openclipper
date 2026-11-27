"""Stage: ask the language model for highlights. Runs in the `llm` queue, one model call per transcript window."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from redis.asyncio import from_url
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.session import make_engine, make_sessionmaker
from app.pipeline.types import StageContext, StageError, StageResult
from app.security.crypto import Vault
from app.services import highlight_engine, highlight_logic
from app.services import highlights as svc
from app.services.llm import LLMService


def params(s: Settings, job_params: dict[str, Any] | None = None) -> dict[str, Any]:
    jp = job_params or {}
    # params_hash already covers the transcript version, the request, the scoring settings and the model route
    return {"v": 1, "run": jp.get("params_hash"), "transcript": jp.get("transcript_id")}


def run(ctx: StageContext) -> StageResult:
    # Stages run in a worker thread; the model service is async, so this stage owns a small event loop of its own.
    return asyncio.run(_run(ctx))


def _read_json(ctx: StageContext, key: str | None) -> Any:
    if not key:
        return None
    try:
        with ctx.storage.local_path(key) as p:
            return json.loads(p.read_text())
    except (OSError, ValueError):
        return None  # an unreadable optional signal is left out of scoring, never a reason to fail


async def _run(ctx: StageContext) -> StageResult:
    jp = ctx.job_params
    try:
        transcript_id = uuid.UUID(jp["transcript_id"])
        request, cfg = jp["request"], jp["settings"]
        params_hash = jp["params_hash"]
    except (KeyError, ValueError) as exc:
        raise StageError("bad_job", "This highlight job is missing its parameters.") from exc
    user_id = uuid.UUID(jp["user_id"]) if jp.get("user_id") else None

    scenes = _read_json(ctx, ctx.inputs.get("detect_scenes"))
    cuts = [int(c) for c in (scenes or {}).get("cuts_ms", [])]
    waveform = _read_json(ctx, ctx.source.waveform_key) or {}
    peaks = [[int(a), int(b)] for a, b in waveform.get("peaks", [])] if waveform.get("has_audio", True) else []

    engine = make_engine(ctx.settings.database_url, poolclass=NullPool)
    redis = from_url(ctx.settings.redis_url, decode_responses=True)
    try:
        sm = make_sessionmaker(engine)
        async with sm() as s:
            t = await svc.load_view(s, transcript_id)
        llm = LLMService(sm, redis, Vault.from_settings(ctx.settings), ctx.settings)
        kept, stats = await highlight_engine.detect(
            llm=llm,
            t=t,
            request=request,
            cfg=cfg,
            cuts_ms=cuts,
            peaks=peaks,
            user_id=user_id,
            progress=ctx.progress,
            cancelled=ctx.cancelled,
        )
    finally:
        await redis.aclose()
        await engine.dispose()
    return StageResult(
        highlights=highlight_logic.RunData(
            str(transcript_id), {"request": request, "settings": cfg}, params_hash, kept, stats, t
        )
    )
