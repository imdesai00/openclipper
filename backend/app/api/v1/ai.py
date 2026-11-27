"""Admin API for AI providers, models, routing and usage. Secrets go in, masks come out."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.adapters.llm.ollama import OllamaProvider
from app.api.deps import DbDep, RedisDep, SettingsDep, client_ip, require_role
from app.db.models import AiModel, AiProvider, PriceEntry, User
from app.errors import AppError, not_found
from app.ports.llm import ChatMessage, LLMError
from app.schemas.ai import (
    CredentialIn,
    DiscoverOut,
    ModeIn,
    ModelOut,
    ModelPatch,
    PriceIn,
    ProviderIn,
    ProviderOut,
    ProviderPatch,
    PullIn,
    RoutesIn,
    TestOut,
    TestPromptIn,
    TestPromptOut,
    UsageRowOut,
)
from app.services import ai_config, ai_providers
from app.services.llm import LLMService, effective_price, get_mode

router = APIRouter(tags=["ai"])
AdminDep = Annotated[User, Depends(require_role("admin"))]


def _out(v: ai_providers.ProviderView) -> ProviderOut:
    p = v.provider
    return ProviderOut(
        id=p.id,
        kind=p.kind,
        name=p.name,
        base_url=p.base_url,
        enabled=p.enabled,
        is_local=p.is_local,
        rate_limit_rpm=p.rate_limit_rpm,
        timeout_s=p.timeout_s,
        has_credential=v.credential_mask is not None,
        credential_mask=v.credential_mask,
        model_count=v.model_count,
        last_test_at=p.last_test_at,
        last_test_ok=p.last_test_ok,
        last_test_message=p.last_test_message,
    )


def _vault(request: Request) -> Any:
    return request.app.state.vault


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(_: AdminDep, session: DbDep) -> list[ProviderOut]:
    return [_out(v) for v in await ai_providers.list_providers(session)]


@router.get("/providers/presets")
async def presets(_: AdminDep, settings: SettingsDep) -> list[dict[str, Any]]:
    """Starting points for the 'add provider' form. Nothing here is a secret."""
    return [
        {
            "kind": "ollama",
            "name": "Ollama (included)",
            "base_url": settings.ollama_base_url,
            "is_local": True,
            "needs_key": False,
            "note": "Runs on this machine. Free. Download a model after adding it.",
        },
        {
            "kind": "openrouter",
            "name": "OpenRouter",
            "base_url": None,
            "is_local": False,
            "needs_key": True,
            "note": "One key, many models, including some free ones (rate-limited, availability not guaranteed).",
        },
        {"kind": "openai", "name": "OpenAI", "base_url": None, "is_local": False, "needs_key": True, "note": "Paid."},
        {
            "kind": "anthropic",
            "name": "Anthropic",
            "base_url": None,
            "is_local": False,
            "needs_key": True,
            "note": "Paid.",
        },
        {
            "kind": "gemini",
            "name": "Google Gemini",
            "base_url": None,
            "is_local": False,
            "needs_key": True,
            "note": "Free tier and paid.",
        },
        {
            "kind": "groq",
            "name": "Groq",
            "base_url": None,
            "is_local": False,
            "needs_key": True,
            "note": "Fast hosted models; free tier is rate-limited.",
        },
        {
            "kind": "openai_compat",
            "name": "Custom (OpenAI-compatible)",
            "base_url": "http://localhost:1234/v1",
            "is_local": True,
            "needs_key": False,
            "note": "LM Studio, vLLM, llama.cpp server, or any OpenAI-style endpoint.",
        },
    ]


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(body: ProviderIn, request: Request, admin: AdminDep, session: DbDep) -> ProviderOut:
    p = await ai_providers.create_provider(
        session,
        _vault(request),
        admin,
        kind=body.kind,
        name=body.name,
        base_url=body.base_url,
        is_local=body.is_local,
        rate_limit_rpm=body.rate_limit_rpm,
        timeout_s=body.timeout_s,
        api_key=body.api_key,
        ip=client_ip(request),
    )
    return _out(await ai_providers.view(session, p))


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID, body: ProviderPatch, request: Request, admin: AdminDep, session: DbDep
) -> ProviderOut:
    p = await ai_providers.get_provider(session, provider_id)
    await ai_providers.update_provider(
        session,
        admin,
        p,
        name=body.name,
        base_url=body.base_url,
        enabled=body.enabled,
        rate_limit_rpm=body.rate_limit_rpm,
        timeout_s=body.timeout_s,
        is_local=body.is_local,
        set_rate_limit="rate_limit_rpm" in body.model_fields_set,
        ip=client_ip(request),
    )
    return _out(await ai_providers.view(session, p))


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: uuid.UUID, request: Request, admin: AdminDep, session: DbDep) -> Response:
    await ai_providers.delete_provider(
        session, admin, await ai_providers.get_provider(session, provider_id), client_ip(request)
    )
    return Response(status_code=204)


@router.put("/providers/{provider_id}/credential", response_model=ProviderOut)
async def set_credential(
    provider_id: uuid.UUID, body: CredentialIn, request: Request, admin: AdminDep, session: DbDep
) -> ProviderOut:
    p = await ai_providers.get_provider(session, provider_id)
    await ai_providers.set_credential(session, _vault(request), admin, p, body.api_key, client_ip(request))
    return _out(await ai_providers.view(session, p))


@router.delete("/providers/{provider_id}/credential", response_model=ProviderOut)
async def clear_credential(provider_id: uuid.UUID, request: Request, admin: AdminDep, session: DbDep) -> ProviderOut:
    p = await ai_providers.get_provider(session, provider_id)
    await ai_providers.clear_credential(session, admin, p, client_ip(request))
    return _out(await ai_providers.view(session, p))


@router.post("/providers/{provider_id}/test", response_model=TestOut)
async def test_provider(provider_id: uuid.UUID, request: Request, admin: AdminDep, session: DbDep) -> TestOut:
    p = await ai_providers.get_provider(session, provider_id)
    o = await ai_providers.test_provider(session, _vault(request), admin, p, client_ip(request))
    return TestOut(ok=o.ok, message=o.message, latency_ms=o.latency_ms)


@router.post("/providers/{provider_id}/discover", response_model=DiscoverOut)
async def discover(provider_id: uuid.UUID, request: Request, admin: AdminDep, session: DbDep) -> DiscoverOut:
    p = await ai_providers.get_provider(session, provider_id)
    added, updated = await ai_providers.discover_models(session, _vault(request), admin, p, client_ip(request))
    return DiscoverOut(added=added, updated=updated)


@router.post("/providers/{provider_id}/pull")
async def pull_model(
    provider_id: uuid.UUID, body: PullIn, request: Request, _: AdminDep, session: DbDep
) -> StreamingResponse:
    """Download an Ollama model, streaming progress as NDJSON so the UI can show a progress bar."""
    p = await ai_providers.get_provider(session, provider_id)
    if p.kind != "ollama":
        raise AppError("not_ollama", "Only Ollama providers can download models.", 400)
    adapter = OllamaProvider(p.base_url or "http://localhost:11434")

    async def lines() -> AsyncIterator[bytes]:
        try:
            async for ev in adapter.pull(body.model):
                yield (json.dumps(ev) + "\n").encode()
        except LLMError as exc:
            yield (json.dumps({"error": exc.message}) + "\n").encode()
        except Exception:
            yield (json.dumps({"error": "The download was interrupted."}) + "\n").encode()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


# ------------------------------------------------------------------------------------------------ models
async def _model_out(session: DbDep, m: AiModel, p: AiProvider, user_price: PriceEntry | None) -> ModelOut:
    price = effective_price(m, user_price, p)
    source = (
        "local"
        if p.is_local
        else "user"
        if user_price
        else "provider"
        if m.provider_input_per_mtok is not None
        else "free"
        if m.is_free
        else "unknown"
    )
    return ModelOut(
        id=m.id,
        provider_id=p.id,
        provider_name=p.name,
        model_id=m.model_id,
        display_name=m.display_name,
        context_length=m.context_length,
        supports_json_schema=m.supports_json_schema,
        is_free=m.is_free,
        enabled=m.enabled,
        is_local=p.is_local,
        input_per_mtok=price[0] if price else None,
        output_per_mtok=price[1] if price else None,
        price_source=source,
    )


@router.get("/models", response_model=list[ModelOut])
async def list_models(
    _: AdminDep, session: DbDep, provider_id: uuid.UUID | None = None, limit: Annotated[int, Query(ge=1, le=2000)] = 500
) -> list[ModelOut]:
    q = (
        select(AiModel, AiProvider)
        .join(AiProvider, AiProvider.id == AiModel.provider_id)
        .order_by(AiProvider.name, AiModel.model_id)
        .limit(limit)
    )
    if provider_id:
        q = q.where(AiModel.provider_id == provider_id)
    prices = {e.model_id: e for e in (await session.execute(select(PriceEntry))).scalars()}
    return [await _model_out(session, m, p, prices.get(m.id)) for m, p in (await session.execute(q)).all()]


async def _get_model(session: DbDep, model_id: uuid.UUID) -> tuple[AiModel, AiProvider]:
    m = await session.get(AiModel, model_id)
    if m is None:
        raise not_found("Model")
    return m, await ai_providers.get_provider(session, m.provider_id)


@router.patch("/models/{model_id}", response_model=ModelOut)
async def patch_model(model_id: uuid.UUID, body: ModelPatch, _: AdminDep, session: DbDep) -> ModelOut:
    m, p = await _get_model(session, model_id)
    m.enabled = body.enabled
    await session.commit()
    up = (await session.execute(select(PriceEntry).where(PriceEntry.model_id == m.id))).scalar_one_or_none()
    return await _model_out(session, m, p, up)


@router.put("/models/{model_id}/price", response_model=ModelOut)
async def put_price(model_id: uuid.UUID, body: PriceIn, request: Request, admin: AdminDep, session: DbDep) -> ModelOut:
    m, p = await _get_model(session, model_id)
    e = await ai_config.set_price(session, admin, m, body.input_per_mtok, body.output_per_mtok, client_ip(request))
    return await _model_out(session, m, p, e)


@router.delete("/models/{model_id}/price", response_model=ModelOut)
async def delete_price(model_id: uuid.UUID, request: Request, admin: AdminDep, session: DbDep) -> ModelOut:
    m, p = await _get_model(session, model_id)
    await ai_config.clear_price(session, admin, m, client_ip(request))
    return await _model_out(session, m, p, None)


# ------------------------------------------------------------------------------------------------ mode, routes
@router.get("/ai/mode")
async def get_ai_mode(_: AdminDep, session: DbDep) -> dict[str, str]:
    return {"mode": await get_mode(session)}


@router.put("/ai/mode")
async def put_ai_mode(body: ModeIn, request: Request, admin: AdminDep, session: DbDep) -> dict[str, str]:
    return {"mode": await ai_config.set_mode(session, admin, body.mode, client_ip(request))}


@router.get("/ai/routes")
async def get_routes(_: AdminDep, session: DbDep) -> dict[str, Any]:
    return {"mode": await get_mode(session), "routes": await ai_config.routes(session)}


@router.put("/ai/routes")
async def put_routes(body: RoutesIn, request: Request, admin: AdminDep, session: DbDep) -> dict[str, Any]:
    await ai_config.set_routes(session, admin, {str(k): v for k, v in body.routes.items()}, client_ip(request))
    return {"mode": await get_mode(session), "routes": await ai_config.routes(session)}


# ------------------------------------------------------------------------------------------------ test prompt, usage
class _Json(BaseModel):
    model_config = {"extra": "allow"}


@router.post("/ai/test-prompt", response_model=TestPromptOut)
async def test_prompt(
    body: TestPromptIn, request: Request, admin: AdminDep, redis: RedisDep, settings: SettingsDep
) -> TestPromptOut:
    svc = LLMService(
        request.app.state.sessionmaker,
        redis,
        _vault(request),
        settings,
        getattr(request.app.state, "llm_transport", None),
    )
    c = await svc.complete(
        task=body.task,
        messages=[ChatMessage("user", body.prompt)],
        user_id=admin.id,
        max_tokens=body.max_tokens,
        temperature=0.2,
        model_id=body.model_id,
        schema_model=_Json if body.json_mode else None,
    )
    return TestPromptOut(
        text=c.text,
        provider=c.provider_name,
        model=c.model_name,
        tokens_in=c.tokens_in,
        tokens_out=c.tokens_out,
        cost_est=c.cost_est,
        latency_ms=c.latency_ms,
        repairs=c.repairs,
        failed_attempts=[a.__dict__ for a in c.attempts],
    )


@router.get("/usage")
async def usage(_: AdminDep, session: DbDep, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
    r = await ai_config.usage_report(session, days)
    r["recent"] = [UsageRowOut.model_validate(x) for x in r["recent"]]
    return r
