"""OpenAI-style /chat/completions + /models. Serves OpenAI, OpenRouter, Groq and any compatible server
(LM Studio, vLLM, llama.cpp, Ollama's /v1, ...). The API key travels only in the Authorization header."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.adapters.llm.http import client, json_or_error, map_transport_error, raise_for_status
from app.ports.llm import ChatRequest, ChatResult, LLMError, ModelInfo

DEFAULT_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def _per_mtok(per_token: Any) -> Decimal | None:
    try:
        return (Decimal(str(per_token)) * 1_000_000).normalize()
    except (InvalidOperation, TypeError):
        return None


class OpenAICompatProvider:
    def __init__(
        self,
        kind: str,
        base_url: str | None,
        api_key: str | None,
        timeout_s: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.kind = kind
        self.base = (base_url or DEFAULT_URLS.get(kind, "")).rstrip("/")
        self.key = api_key
        self.timeout = timeout_s
        self.transport = transport
        # Servers that accept `response_format: json_schema`. Others fall back to prompt + validate + repair.
        self.native_schema = kind in ("openai", "openrouter", "openai_compat")

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def _secrets(self) -> list[str]:
        return [self.key] if self.key else []

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.base:
            raise LLMError("bad_config", "This provider has no base URL.")
        try:
            async with client(self.timeout, self.transport) as c:
                resp = await c.request(method, f"{self.base}{path}", headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        raise_for_status(resp, self._secrets())
        return json_or_error(resp)

    async def chat(self, req: ChatRequest) -> ChatResult:
        body: dict[str, Any] = {
            "model": req.model,
            "temperature": req.temperature,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        }
        if req.max_tokens:
            body["max_tokens"] = req.max_tokens
        enforced = False
        if req.json_schema and self.native_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": req.json_schema, "strict": False},
            }
            enforced = True
        try:
            data = await self._request("POST", "/chat/completions", body)
        except LLMError as exc:
            if enforced and exc.code == "bad_request":  # this model/server does not do json_schema
                raise LLMError("schema_unsupported", "The model does not support schema-constrained output.") from exc
            raise
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("bad_response", "The provider returned no choices.", retryable=True)
        msg = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        return ChatResult(
            text=str(msg.get("content") or ""),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            finish_reason=choices[0].get("finish_reason"),
            schema_enforced=enforced,
        )

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/models")
        out: list[ModelInfo] = []
        for m in data.get("data", []):
            mid = m.get("id")
            if not mid:
                continue
            info = ModelInfo(
                model_id=str(mid), display_name=m.get("name") or str(mid), supports_json_schema=self.native_schema
            )
            ctx = m.get("context_length") or m.get("context_window")  # OpenRouter / Groq
            info.context_length = int(ctx) if isinstance(ctx, int | float) else None
            pricing = m.get("pricing")
            if isinstance(pricing, dict):  # OpenRouter publishes USD per token as strings
                info.input_per_mtok = _per_mtok(pricing.get("prompt"))
                info.output_per_mtok = _per_mtok(pricing.get("completion"))
                info.is_free = info.input_per_mtok == 0 and info.output_per_mtok == 0
            if str(mid).endswith(":free"):
                info.is_free = True
            if self.kind == "openrouter":
                params = m.get("supported_parameters")
                info.supports_json_schema = isinstance(params, list) and "structured_outputs" in params
            out.append(info)
        return out

    async def check(self) -> str:
        models = await self.list_models()
        return f"Reachable, {len(models)} models listed"
