"""Anthropic Messages API. Structured output uses a forced tool call whose input_schema is the JSON schema."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.adapters.llm.http import client, json_or_error, map_transport_error, raise_for_status
from app.ports.llm import ChatRequest, ChatResult, ModelInfo

DEFAULT_URL = "https://api.anthropic.com"
VERSION = "2023-06-01"


class AnthropicProvider:
    kind = "anthropic"
    native_schema = True

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        timeout_s: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base = (base_url or DEFAULT_URL).rstrip("/")
        self.key = api_key
        self.timeout = timeout_s
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        h = {"anthropic-version": VERSION, "content-type": "application/json"}
        if self.key:
            h["x-api-key"] = self.key
        return h

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with client(self.timeout, self.transport) as c:
                resp = await c.request(method, f"{self.base}{path}", headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        raise_for_status(resp, [self.key or ""])
        return json_or_error(resp)

    async def chat(self, req: ChatRequest) -> ChatResult:
        system = "\n\n".join(m.content for m in req.messages if m.role == "system")
        msgs = [{"role": m.role, "content": m.content} for m in req.messages if m.role != "system"]
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens or 1024,
            "temperature": req.temperature,
            "messages": msgs,
        }
        if system:
            body["system"] = system
        if req.json_schema:
            body["tools"] = [{"name": "result", "description": "Return the result.", "input_schema": req.json_schema}]
            body["tool_choice"] = {"type": "tool", "name": "result"}
        data = await self._request("POST", "/v1/messages", body)
        text, enforced = "", False
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and req.json_schema:
                text, enforced = json.dumps(block.get("input", {})), True
                break
            if block.get("type") == "text":
                text += str(block.get("text", ""))
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            tokens_in=int(usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("output_tokens") or 0),
            finish_reason=data.get("stop_reason"),
            schema_enforced=enforced,
        )

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/v1/models?limit=100")
        out = []
        for m in data.get("data", []):
            if m.get("id"):
                ctx = m.get("max_input_tokens")
                out.append(
                    ModelInfo(
                        model_id=str(m["id"]),
                        display_name=m.get("display_name") or str(m["id"]),
                        context_length=int(ctx) if isinstance(ctx, int) else None,
                        supports_json_schema=True,
                    )
                )
        return out

    async def check(self) -> str:
        return f"Reachable, {len(await self.list_models())} models listed"
