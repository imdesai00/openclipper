"""Ollama native API (/api/chat, /api/tags, /api/show). Local, free, supports schema-constrained output."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.adapters.llm.http import client, json_or_error, map_transport_error, raise_for_status
from app.ports.llm import ChatRequest, ChatResult, LLMError, ModelInfo


class OllamaProvider:
    kind = "ollama"
    native_schema = True

    def __init__(
        self, base_url: str, timeout_s: float = 120, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout_s
        self.transport = transport

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with client(self.timeout, self.transport) as c:
                resp = await c.post(f"{self.base}{path}", json=body)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        raise_for_status(resp, [])
        return json_or_error(resp)

    async def chat(self, req: ChatRequest) -> ChatResult:
        options: dict[str, Any] = {"temperature": req.temperature}
        if req.max_tokens:
            options["num_predict"] = req.max_tokens
        if req.num_ctx:
            options["num_ctx"] = req.num_ctx
        body: dict[str, Any] = {
            "model": req.model,
            "stream": False,
            "options": options,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        }
        if req.json_schema:
            body["format"] = req.json_schema
        data = await self._post("/api/chat", body)
        msg = data.get("message") or {}
        return ChatResult(
            text=str(msg.get("content", "")),
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
            finish_reason=data.get("done_reason"),
            schema_enforced=bool(req.json_schema),
        )

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with client(self.timeout, self.transport) as c:
                resp = await c.get(f"{self.base}/api/tags")
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        raise_for_status(resp, [])
        names = [m.get("name") or m.get("model") for m in json_or_error(resp).get("models", [])]
        sem = asyncio.Semaphore(4)

        async def info(name: str) -> ModelInfo:
            async with sem:
                try:
                    show = await self._post("/api/show", {"model": name})
                except LLMError:
                    return ModelInfo(model_id=name, display_name=name, is_free=True, supports_json_schema=True)
            ctx = next(
                (int(v) for k, v in (show.get("model_info") or {}).items() if k.endswith(".context_length")), None
            )
            return ModelInfo(
                model_id=name,
                display_name=name,
                context_length=ctx,
                supports_json_schema=True,
                is_free=True,
                extra={"capabilities": show.get("capabilities") or []},
            )

        return list(await asyncio.gather(*(info(n) for n in names if n)))

    async def check(self) -> str:
        try:
            async with client(min(self.timeout, 15), self.transport) as c:
                resp = await c.get(f"{self.base}/api/version")
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        raise_for_status(resp, [])
        return f"Ollama {json_or_error(resp).get('version', '')}".strip()

    async def pull(self, model: str) -> Any:
        """Yield progress dicts from /api/pull (NDJSON) so the UI can show download progress."""
        import json

        async with (
            client(3600, self.transport) as c,
            c.stream("POST", f"{self.base}/api/pull", json={"model": model, "stream": True}) as resp,
        ):
            if resp.status_code >= 400:
                await resp.aread()
                raise_for_status(resp, [])
            async for line in resp.aiter_lines():
                if line.strip():
                    yield json.loads(line)
