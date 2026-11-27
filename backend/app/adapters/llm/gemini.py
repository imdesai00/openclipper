"""Google Gemini generateContent. The key goes in the x-goog-api-key header, never in the URL.
Structured output is done with prompt + validate + repair (native_schema False): Gemini's schema dialect differs
from JSON Schema and has not been verified against the live API here."""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.llm.http import client, json_or_error, map_transport_error, raise_for_status
from app.ports.llm import ChatRequest, ChatResult, LLMError, ModelInfo

DEFAULT_URL = "https://generativelanguage.googleapis.com"


class GeminiProvider:
    kind = "gemini"
    native_schema = False

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

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        if self.key:
            headers["x-goog-api-key"] = self.key
        try:
            async with client(self.timeout, self.transport) as c:
                resp = await c.request(method, f"{self.base}{path}", headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        raise_for_status(resp, [self.key or ""])
        return json_or_error(resp)

    async def chat(self, req: ChatRequest) -> ChatResult:
        system = "\n\n".join(m.content for m in req.messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in req.messages
            if m.role != "system"
        ]
        gen: dict[str, Any] = {"temperature": req.temperature}
        if req.max_tokens:
            gen["maxOutputTokens"] = req.max_tokens
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        model = req.model.removeprefix("models/")
        data = await self._request("POST", f"/v1beta/models/{model}:generateContent", body)
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError("blocked", f"Gemini returned no answer ({reason}).")
        parts = (cands[0].get("content") or {}).get("parts") or []
        usage = data.get("usageMetadata") or {}
        return ChatResult(
            text="".join(str(p.get("text", "")) for p in parts),
            tokens_in=int(usage.get("promptTokenCount") or 0),
            tokens_out=int(usage.get("candidatesTokenCount") or 0),
            finish_reason=cands[0].get("finishReason"),
        )

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/v1beta/models?pageSize=200")
        out = []
        for m in data.get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            name = str(m.get("name", "")).removeprefix("models/")
            if name:
                lim = m.get("inputTokenLimit")
                out.append(
                    ModelInfo(
                        model_id=name,
                        display_name=m.get("displayName") or name,
                        context_length=int(lim) if isinstance(lim, int) else None,
                    )
                )
        return out

    async def check(self) -> str:
        return f"Reachable, {len(await self.list_models())} models listed"
