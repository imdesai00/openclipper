"""Run a prompt against the configured model route (SPEC §6.I).

Order of business for one call: resolve the route for the task -> apply LOCAL/REMOTE/HYBRID mode -> for each model in
the fallback chain: skip if its circuit breaker is open or it is over its own rate limit, otherwise try up to N
times with jittered backoff -> log every attempt, successful or not -> on schema requests, validate and repair.
Nothing fails silently:
if every model fails, the caller gets one error that lists each attempt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import AiModel, AiProvider, AiUsageLog, PriceEntry, Setting, TaskRoute
from app.errors import AppError
from app.logging_setup import request_id_var
from app.ports.llm import ChatMessage, ChatRequest, LLMError
from app.security import ratelimit
from app.security.crypto import Vault
from app.services import ai_providers

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
MODES = ("LOCAL", "REMOTE", "HYBRID")
MAX_REPAIRS = 2
# Models that rejected response_format=json_schema. Process-wide so the next request does not retry the doomed variant.
_NO_NATIVE_SCHEMA: set[uuid.UUID] = set()
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


async def get_mode(session: AsyncSession) -> str:
    row = (
        await session.execute(select(Setting).where(Setting.scope == "global", Setting.key == "ai"))
    ).scalar_one_or_none()
    mode = (row.value or {}).get("mode") if row else None
    return mode if mode in MODES else "LOCAL"  # default: nothing leaves this machine until an admin says so


def mode_allows(mode: str, is_local: bool) -> bool:
    return mode == "HYBRID" or (mode == "LOCAL") == is_local


@dataclass
class Attempt:
    model: str
    provider: str
    code: str
    message: str


@dataclass
class Completion:
    text: str
    parsed: BaseModel | None
    provider_name: str
    model_name: str
    tokens_in: int
    tokens_out: int
    cost_est: Decimal | None
    latency_ms: int
    repairs: int = 0
    attempts: list[Attempt] = field(default_factory=list)  # failed attempts before the one that worked


def effective_price(
    model: AiModel, user_price: PriceEntry | None, provider: AiProvider
) -> tuple[Decimal, Decimal] | None:
    """(input, output) USD per million tokens, or None when nobody knows. Local inference costs nothing."""
    if provider.is_local:
        return Decimal(0), Decimal(0)
    if user_price:
        return user_price.input_per_mtok, user_price.output_per_mtok
    if model.provider_input_per_mtok is not None and model.provider_output_per_mtok is not None:
        return model.provider_input_per_mtok, model.provider_output_per_mtok
    if model.is_free:
        return Decimal(0), Decimal(0)
    return None


def cost_of(price: tuple[Decimal, Decimal] | None, tokens_in: int, tokens_out: int) -> Decimal | None:
    if price is None:
        return None
    return (price[0] * tokens_in + price[1] * tokens_out) / Decimal(1_000_000)


class LLMService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        vault: Vault,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.sm = sessionmaker
        self.redis = redis
        self.vault = vault
        self.settings = settings
        self.transport = transport

    # ------------------------------------------------------------------ routing
    async def chain_for(self, session: AsyncSession, task: str) -> list[tuple[AiModel, AiProvider]]:
        rows = (
            await session.execute(
                select(AiModel, AiProvider)
                .join(TaskRoute, TaskRoute.model_id == AiModel.id)
                .join(AiProvider, AiProvider.id == AiModel.provider_id)
                .where(TaskRoute.task == task)
                .order_by(TaskRoute.position)
            )
        ).all()
        return [(m, p) for m, p in rows]

    async def _resolve(self, task: str, model_id: uuid.UUID | None) -> tuple[list[tuple[AiModel, AiProvider]], str]:
        async with self.sm() as s:
            mode = await get_mode(s)
            if model_id:
                m = await s.get(AiModel, model_id)
                p = await s.get(AiProvider, m.provider_id) if m else None
                chain = [(m, p)] if m and p else []
            else:
                chain = await self.chain_for(s, task)
        if not chain:
            raise AppError("no_model_configured", f"No model is set for '{task}'. Choose one in the AI settings.", 409)
        return chain, mode

    # ------------------------------------------------------------------ circuit breaker + rate limit
    def _bk(self, provider_id: uuid.UUID) -> tuple[str, str]:
        return f"llm:fail:{provider_id}", f"llm:open:{provider_id}"

    async def breaker_open(self, provider_id: uuid.UUID) -> bool:
        return bool(await self.redis.exists(self._bk(provider_id)[1]))

    async def _breaker_failure(self, provider_id: uuid.UUID) -> None:
        fails, opened = self._bk(provider_id)
        n = await self.redis.incr(fails)
        await self.redis.expire(fails, 120)
        if n >= self.settings.llm_breaker_threshold:
            await self.redis.set(
                opened, "1", ex=self.settings.llm_breaker_cooldown_seconds
            )  # half-open when it expires
            await self.redis.delete(fails)
            log.warning("circuit breaker opened", extra={"extra_fields": {"provider_id": str(provider_id)}})

    async def _breaker_success(self, provider_id: uuid.UUID) -> None:
        await self.redis.delete(*self._bk(provider_id))

    # ------------------------------------------------------------------ usage log
    async def _log(
        self,
        *,
        user_id: uuid.UUID | None,
        model: AiModel,
        provider: AiProvider,
        task: str,
        status: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: Decimal | None = None,
        latency_ms: int = 0,
        code: str | None = None,
        message: str | None = None,
        repairs: int = 0,
        attempt: int = 1,
    ) -> None:
        async with self.sm() as s:
            s.add(
                AiUsageLog(
                    user_id=user_id,
                    provider_id=provider.id,
                    model_id=model.id,
                    provider_name=provider.name,
                    model_name=model.model_id,
                    task=task,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_est=cost,
                    latency_ms=latency_ms,
                    status=status,
                    error_code=code,
                    error_message=(message or "")[:1000] or None,
                    repairs=repairs,
                    attempt=attempt,
                    request_id=request_id_var.get(),
                )
            )
            await s.commit()

    # ------------------------------------------------------------------ public API
    async def complete(
        self,
        *,
        task: str,
        messages: list[ChatMessage],
        user_id: uuid.UUID | None = None,
        schema_model: type[T] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model_id: uuid.UUID | None = None,
    ) -> Completion:
        chain, mode = await self._resolve(task, model_id)
        usable = [(m, p) for m, p in chain if p.enabled and m.enabled and mode_allows(mode, p.is_local)]
        skipped = [
            Attempt(m.model_id, p.name, "blocked", self._why_blocked(m, p, mode))
            for m, p in chain
            if (m, p) not in usable
        ]
        if not usable:
            raise AppError(
                "route_unusable",
                "None of the models for this task can be used right now.",
                409,
                details=[a.__dict__ for a in skipped],
            )
        failures: list[Attempt] = list(skipped)
        for model, provider in usable:
            if await self.breaker_open(provider.id):
                failures.append(
                    Attempt(
                        model.model_id,
                        provider.name,
                        "circuit_open",
                        "This provider failed repeatedly and is paused for a short while.",
                    )
                )
                continue
            if provider.rate_limit_rpm:
                ok, _ = await ratelimit.hit(self.redis, f"rl:llm:{provider.id}", provider.rate_limit_rpm, 60)
                if not ok:
                    failures.append(
                        Attempt(
                            model.model_id,
                            provider.name,
                            "local_rate_limit",
                            f"Over the configured limit of {provider.rate_limit_rpm} requests per minute.",
                        )
                    )
                    continue
            try:
                return await self._run_model(
                    model, provider, task, messages, user_id, schema_model, temperature, max_tokens, failures
                )
            except LLMError as exc:
                failures.append(Attempt(model.model_id, provider.name, exc.code, exc.message))
        raise AppError(
            "llm_failed", "No model could answer. " + failures[-1].message, 502, details=[a.__dict__ for a in failures]
        )

    @staticmethod
    def _why_blocked(m: AiModel, p: AiProvider, mode: str) -> str:
        if not p.enabled:
            return f"Provider '{p.name}' is switched off."
        if not m.enabled:
            return "This model is switched off."
        return f"{mode.title()} mode does not allow {'a local' if p.is_local else 'a remote'} provider."

    # ------------------------------------------------------------------ one model: retries, structured output, repair
    async def _run_model(
        self,
        model: AiModel,
        provider: AiProvider,
        task: str,
        messages: list[ChatMessage],
        user_id: uuid.UUID | None,
        schema_model: type[T] | None,
        temperature: float,
        max_tokens: int | None,
        earlier: list[Attempt],
    ) -> Completion:
        async with self.sm() as s:
            adapter = await ai_providers.adapter_for(s, self.vault, provider, self.transport)
            user_price = (
                await s.execute(select(PriceEntry).where(PriceEntry.model_id == model.id))
            ).scalar_one_or_none()
        price = effective_price(model, user_price, provider)
        schema = schema_model.model_json_schema() if schema_model else None
        native = (
            bool(schema) and adapter.native_schema and model.supports_json_schema and model.id not in _NO_NATIVE_SCHEMA
        )
        convo = list(messages)
        if schema and not native:
            convo = self._with_schema_instructions(convo, schema)
        total_in = total_out = 0
        t0 = time.monotonic()
        repairs = 0
        while True:
            req = ChatRequest(
                model=model.model_id,
                messages=convo,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=schema if native else None,
            )
            try:
                res = await self._call_with_retries(adapter, req, model, provider, task, user_id, price, repairs)
            except LLMError as exc:
                if exc.code == "schema_unsupported" and native:  # remember, then do it with prompt + validate + repair
                    _NO_NATIVE_SCHEMA.add(model.id)
                    native = False
                    convo = self._with_schema_instructions(list(messages), schema or {})
                    continue
                raise
            total_in, total_out = total_in + res.tokens_in, total_out + res.tokens_out
            if schema_model is None:
                return self._done(res.text, None, model, provider, total_in, total_out, price, t0, repairs, earlier)
            try:
                parsed = schema_model.model_validate_json(_FENCE.sub("", res.text.strip()))
                return self._done(res.text, parsed, model, provider, total_in, total_out, price, t0, repairs, earlier)
            except (ValidationError, ValueError) as exc:
                if repairs >= MAX_REPAIRS:
                    raise LLMError(
                        "invalid_structured_output", f"The model did not return valid JSON after {MAX_REPAIRS} repairs."
                    ) from exc
                repairs += 1
                convo = [
                    *convo,
                    ChatMessage("assistant", res.text),
                    ChatMessage(
                        "user",
                        f"That was not valid for the required format ({_short(exc)}). "
                        "Reply with only the corrected JSON.",
                    ),
                ]

    def _done(
        self,
        text: str,
        parsed: BaseModel | None,
        model: AiModel,
        provider: AiProvider,
        tin: int,
        tout: int,
        price: tuple[Decimal, Decimal] | None,
        t0: float,
        repairs: int,
        earlier: list[Attempt],
    ) -> Completion:
        return Completion(
            text=text,
            parsed=parsed,
            provider_name=provider.name,
            model_name=model.model_id,
            tokens_in=tin,
            tokens_out=tout,
            cost_est=cost_of(price, tin, tout),
            latency_ms=int((time.monotonic() - t0) * 1000),
            repairs=repairs,
            attempts=list(earlier),
        )

    @staticmethod
    def _with_schema_instructions(msgs: list[ChatMessage], schema: dict[str, Any]) -> list[ChatMessage]:
        note = (
            "Reply with a single JSON object and nothing else, no prose and no code fences. "
            "It must match this JSON Schema:\n" + json.dumps(schema)
        )
        return (
            [*msgs, ChatMessage("system", note)]
            if msgs and msgs[0].role != "system"
            else [msgs[0], ChatMessage("system", note), *msgs[1:]]
        )

    async def _call_with_retries(
        self,
        adapter: Any,
        req: ChatRequest,
        model: AiModel,
        provider: AiProvider,
        task: str,
        user_id: uuid.UUID | None,
        price: tuple[Decimal, Decimal] | None,
        repairs: int,
    ) -> Any:
        last: LLMError | None = None
        for attempt in range(1, self.settings.llm_max_attempts + 1):
            t0 = time.monotonic()
            try:
                res = await adapter.chat(req)
            except LLMError as exc:
                last = exc
                ms = int((time.monotonic() - t0) * 1000)
                await self._log(
                    user_id=user_id,
                    model=model,
                    provider=provider,
                    task=task,
                    status="error",
                    latency_ms=ms,
                    code=exc.code,
                    message=exc.message,
                    attempt=attempt,
                    repairs=repairs,
                )
                if exc.code in (
                    "provider_error",
                    "timeout",
                    "unreachable",
                    "rate_limited",
                    "network_error",
                    "bad_response",
                ):
                    await self._breaker_failure(provider.id)
                if not exc.retryable or attempt == self.settings.llm_max_attempts:
                    raise
                base = self.settings.llm_backoff_base_seconds * 2 ** (attempt - 1)
                await asyncio.sleep(
                    exc.retry_after if exc.retry_after is not None else min(8.0, base) * random.uniform(0.5, 1.5)
                )
                continue
            await self._breaker_success(provider.id)
            await self._log(
                user_id=user_id,
                model=model,
                provider=provider,
                task=task,
                status="ok",
                tokens_in=res.tokens_in,
                tokens_out=res.tokens_out,
                cost=cost_of(price, res.tokens_in, res.tokens_out),
                latency_ms=int((time.monotonic() - t0) * 1000),
                attempt=attempt,
                repairs=repairs,
            )
            return res
        raise last or LLMError("unknown", "The call failed.")


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300]
