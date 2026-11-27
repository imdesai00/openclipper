from __future__ import annotations

import httpx

from app.adapters.llm.anthropic import AnthropicProvider
from app.adapters.llm.gemini import GeminiProvider
from app.adapters.llm.ollama import OllamaProvider
from app.adapters.llm.openai_compat import OpenAICompatProvider
from app.ports.llm import LLMProvider


def build_provider(
    kind: str,
    base_url: str | None,
    api_key: str | None,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LLMProvider:
    if kind == "ollama":
        return OllamaProvider(base_url or "http://localhost:11434", timeout_s, transport)
    if kind == "anthropic":
        return AnthropicProvider(base_url, api_key, timeout_s, transport)
    if kind == "gemini":
        return GeminiProvider(base_url, api_key, timeout_s, transport)
    return OpenAICompatProvider(kind, base_url, api_key, timeout_s, transport)
