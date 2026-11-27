"""Stage: ask the language model for each platform's title, description, hashtags and tags, one platform at a time.

Each platform is generated independently (SPEC 6.H) from the clip's own words. One platform failing does not lose the
others; every failure is recorded and shown."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from redis.asyncio import from_url
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.session import make_engine, make_sessionmaker
from app.errors import AppError
from app.pipeline.types import MetadataData, StageContext, StageError, StageResult
from app.ports.llm import ChatMessage
from app.security.crypto import Vault
from app.services import clip_metadata_logic as ml
from app.services.llm import LLMService

TASK = "metadata"
FALLBACK_CONTEXT = 4096
RESERVE_TOKENS = 1900  # instructions and the answer


def params(s: Settings, job_params: dict[str, Any] | None = None) -> dict[str, Any]:
    jp = job_params or {}
    return {"v": 1, "clip": jp.get("clip_id"), "platforms": jp.get("platforms"), "nonce": jp.get("nonce")}


def run(ctx: StageContext) -> StageResult:
    return asyncio.run(_run(ctx))  # stages run in a worker thread; the model service is async


def fit_text(text: str, contexts: list[int | None]) -> tuple[str, int]:
    """The part of the transcript that fits the smallest usable model, and the context size to ask the server for."""
    known = [c for c in contexts if c]
    ctx = min(known) if known else FALLBACK_CONTEXT
    if len(known) < len(contexts):
        ctx = min(ctx, FALLBACK_CONTEXT)
    budget_chars = max(600, (ctx - RESERVE_TOKENS) * 3)
    cut = text if len(text) <= budget_chars else text[:budget_chars].rsplit(" ", 1)[0] + " …"
    need = len(cut) // 3 + RESERVE_TOKENS
    return cut, min(ctx, ((need + 511) // 512) * 512)


async def _run(ctx: StageContext) -> StageResult:
    jp = ctx.job_params
    try:
        platforms = [p for p in jp["platforms"] if p in ml.PLATFORMS]
        text, clip_id = str(jp["text"]), str(jp["clip_id"])
    except KeyError as exc:
        raise StageError("bad_job", "This metadata job is missing its parameters.") from exc
    user_id = uuid.UUID(jp["user_id"]) if jp.get("user_id") else None
    engine = make_engine(ctx.settings.database_url, poolclass=NullPool)
    redis = from_url(ctx.settings.redis_url, decode_responses=True)
    items: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    who = ""
    try:
        llm = LLMService(make_sessionmaker(engine), redis, Vault.from_settings(ctx.settings), ctx.settings)
        try:
            contexts = await llm.usable_contexts(TASK)
        except AppError as exc:
            raise StageError(exc.code, exc.message) from exc
        if not contexts:
            raise StageError("route_unusable", "None of the models for metadata can be used right now.")
        excerpt, num_ctx = fit_text(text, contexts)
        for i, platform in enumerate(platforms):
            if ctx.cancelled():
                raise StageError("cancelled", "Cancelled")
            ctx.progress(i / max(1, len(platforms)), f"Writing the {platform} text")
            msgs = ml.build_messages(
                platform,
                clip_title=str(jp.get("clip_title", "")),
                source_title=str(jp.get("source_title", "")),
                text=excerpt,
            )
            try:
                c = await llm.complete(
                    task=TASK,
                    messages=[ChatMessage(r, t) for r, t in msgs],
                    user_id=user_id,
                    schema_model=ml.meta_model(platform),
                    temperature=0.4,
                    max_tokens=1200,
                    num_ctx=num_ctx,
                )
            except AppError as exc:
                if exc.code in ("no_model_configured", "route_unusable"):
                    raise StageError(exc.code, exc.message) from exc
                errors[platform] = exc.message
                continue
            assert c.parsed is not None
            items[platform] = ml.clean(platform, c.parsed)
            who = f"{c.provider_name} / {c.model_name}"
    finally:
        await redis.aclose()
        await engine.dispose()
    if not items:
        first = next(iter(errors.values()), "No model could answer.")
        raise StageError("llm_failed", f"No metadata could be generated. {first}")
    return StageResult(metadata=MetadataData(clip_id, items, errors, who, bool(jp.get("overwrite"))))
