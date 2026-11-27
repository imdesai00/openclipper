"""Remote provider adapters against FIXTURES (httpx.MockTransport). These prove how we build requests, where
credentials go, how we parse answers and map errors. They do NOT prove the live services behave the same:
that is the `external` tier (tests/integration/test_llm_external.py), SKIPPED without API keys."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.llm.anthropic import AnthropicProvider
from app.adapters.llm.factory import build_provider
from app.adapters.llm.gemini import GeminiProvider
from app.adapters.llm.http import redact
from app.adapters.llm.ollama import OllamaProvider
from app.adapters.llm.openai_compat import OpenAICompatProvider
from app.ports.llm import ChatMessage, ChatRequest, LLMError

pytestmark = pytest.mark.unit
KEY = "sk-live-SECRET-0123456789abcdef"
SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
REQ = ChatRequest(model="m1", messages=[ChatMessage("system", "Be brief."), ChatMessage("user", "Hi")], max_tokens=50)


class Recorder:
    def __init__(self, responder: Any) -> None:
        self.calls: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)
        self._responder = responder

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._responder(request)

    def body(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.calls[i].content)


def ok(data: dict[str, Any], status: int = 200) -> Any:
    return lambda _r: httpx.Response(status, json=data)


# ----------------------------------------------------------------------------------------------- OpenAI family
OPENAI_OK = {
    "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
}


async def test_openai_request_shape_and_parsing() -> None:
    rec = Recorder(ok(OPENAI_OK))
    p = OpenAICompatProvider("openai", None, KEY, transport=rec.transport)
    res = await p.chat(REQ)
    assert (res.text, res.tokens_in, res.tokens_out, res.finish_reason) == ("Hello!", 12, 3, "stop")
    call = rec.calls[0]
    assert str(call.url) == "https://api.openai.com/v1/chat/completions"
    assert call.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(call.url)  # never in the URL
    assert rec.body()["messages"][0] == {"role": "system", "content": "Be brief."} and rec.body()["max_tokens"] == 50


async def test_openai_structured_output_uses_json_schema_response_format() -> None:
    rec = Recorder(ok({**OPENAI_OK, "choices": [{"message": {"content": '{"title":"x"}'}}]}))
    res = await OpenAICompatProvider("openai", None, KEY, transport=rec.transport).chat(
        ChatRequest("m1", REQ.messages, json_schema=SCHEMA)
    )
    assert (
        rec.body()["response_format"]["type"] == "json_schema"
        and rec.body()["response_format"]["json_schema"]["schema"] == SCHEMA
    )
    assert res.schema_enforced is True


async def test_groq_does_not_send_json_schema_and_uses_its_own_base_url() -> None:
    rec = Recorder(ok(OPENAI_OK))
    p = OpenAICompatProvider("groq", None, KEY, transport=rec.transport)
    res = await p.chat(ChatRequest("m1", REQ.messages, json_schema=SCHEMA))
    assert "response_format" not in rec.body() and res.schema_enforced is False and p.native_schema is False
    assert str(rec.calls[0].url).startswith("https://api.groq.com/openai/v1/")


async def test_a_model_that_rejects_json_schema_is_reported_as_such() -> None:
    rec = Recorder(ok({"error": {"message": "response_format json_schema unsupported"}}, 400))
    with pytest.raises(LLMError) as e:
        await OpenAICompatProvider("openai", None, KEY, transport=rec.transport).chat(
            ChatRequest("m1", REQ.messages, json_schema=SCHEMA)
        )
    assert e.value.code == "schema_unsupported" and not e.value.retryable


async def test_openrouter_model_list_exposes_prices_free_flag_and_context() -> None:
    data = {"data": [
        {"id": "meta/llama-free:free", "name": "Llama (free)", "context_length": 131072, "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["temperature", "structured_outputs"]},
        {"id": "acme/pro", "name": "Pro", "context_length": 200000, "pricing": {"prompt": "0.000003", "completion": "0.000015"},
         "supported_parameters": ["temperature"]},
    ]}  # fmt: skip
    models = await OpenAICompatProvider("openrouter", None, KEY, transport=Recorder(ok(data)).transport).list_models()
    free, pro = models
    assert free.is_free and free.context_length == 131072 and free.supports_json_schema
    assert (
        (pro.input_per_mtok, pro.output_per_mtok) == (Decimal("3"), Decimal("15"))
        and not pro.is_free
        and not pro.supports_json_schema
    )


async def test_groq_model_list_reads_context_window() -> None:
    data = {"data": [{"id": "llama-3.3-70b", "context_window": 131072}]}
    (m,) = await OpenAICompatProvider("groq", None, KEY, transport=Recorder(ok(data)).transport).list_models()
    assert m.model_id == "llama-3.3-70b" and m.context_length == 131072


async def test_custom_server_without_a_key_sends_no_authorization_header() -> None:
    rec = Recorder(ok(OPENAI_OK))
    await OpenAICompatProvider("openai_compat", "http://lmstudio.local:1234/v1/", None, transport=rec.transport).chat(
        REQ
    )
    assert (
        "authorization" not in rec.calls[0].headers
        and str(rec.calls[0].url) == "http://lmstudio.local:1234/v1/chat/completions"
    )


# ----------------------------------------------------------------------------------------------- Anthropic
async def test_anthropic_request_shape_system_prompt_and_usage() -> None:
    rec = Recorder(
        ok(
            {
                "content": [{"type": "text", "text": "Hello"}],
                "usage": {"input_tokens": 9, "output_tokens": 2},
                "stop_reason": "end_turn",
            }
        )
    )
    res = await AnthropicProvider(None, KEY, transport=rec.transport).chat(REQ)
    call = rec.calls[0]
    assert str(call.url) == "https://api.anthropic.com/v1/messages"
    assert (
        call.headers["x-api-key"] == KEY
        and call.headers["anthropic-version"] == "2023-06-01"
        and KEY not in str(call.url)
    )
    assert rec.body()["system"] == "Be brief." and rec.body()["messages"] == [{"role": "user", "content": "Hi"}]
    assert (res.text, res.tokens_in, res.tokens_out) == ("Hello", 9, 2)


async def test_anthropic_structured_output_is_a_forced_tool_call() -> None:
    rec = Recorder(
        ok(
            {
                "content": [{"type": "tool_use", "name": "result", "input": {"title": "x"}}],
                "usage": {"input_tokens": 5, "output_tokens": 4},
            }
        )
    )
    res = await AnthropicProvider(None, KEY, transport=rec.transport).chat(
        ChatRequest("m1", REQ.messages, json_schema=SCHEMA)
    )
    assert (
        rec.body()["tool_choice"] == {"type": "tool", "name": "result"}
        and rec.body()["tools"][0]["input_schema"] == SCHEMA
    )
    assert json.loads(res.text) == {"title": "x"} and res.schema_enforced


async def test_anthropic_model_list() -> None:
    data = {"data": [{"id": "claude-x", "display_name": "Claude X", "type": "model"}]}
    (m,) = await AnthropicProvider(None, KEY, transport=Recorder(ok(data)).transport).list_models()
    assert (m.model_id, m.display_name) == ("claude-x", "Claude X")


# ----------------------------------------------------------------------------------------------- Gemini
async def test_gemini_key_is_a_header_not_a_query_parameter() -> None:
    rec = Recorder(ok({"candidates": [{"content": {"parts": [{"text": "Hi "}, {"text": "there"}]}, "finishReason": "STOP"}],
                       "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2}}))  # fmt: skip
    res = await GeminiProvider(None, KEY, transport=rec.transport).chat(
        ChatRequest("models/gemini-x", REQ.messages, max_tokens=20)
    )
    call = rec.calls[0]
    assert str(call.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    assert call.headers["x-goog-api-key"] == KEY and "key=" not in str(call.url)
    assert (
        rec.body()["systemInstruction"]["parts"][0]["text"] == "Be brief."
        and rec.body()["contents"][0]["role"] == "user"
    )
    assert (res.text, res.tokens_in, res.tokens_out) == ("Hi there", 7, 2)


async def test_gemini_blocked_prompt_is_a_clear_error() -> None:
    with pytest.raises(LLMError) as e:
        await GeminiProvider(
            None, KEY, transport=Recorder(ok({"promptFeedback": {"blockReason": "SAFETY"}})).transport
        ).chat(REQ)
    assert e.value.code == "blocked" and "SAFETY" in e.value.message


async def test_gemini_lists_only_models_that_can_generate() -> None:
    data = {"models": [{"name": "models/g1", "displayName": "G1", "inputTokenLimit": 1048576, "supportedGenerationMethods": ["generateContent"]},
                       {"name": "models/emb", "supportedGenerationMethods": ["embedContent"]}]}  # fmt: skip
    models = await GeminiProvider(None, KEY, transport=Recorder(ok(data)).transport).list_models()
    assert [(m.model_id, m.context_length) for m in models] == [("g1", 1048576)]


# ----------------------------------------------------------------------------------------------- Ollama (shapes)
async def test_ollama_chat_request_and_token_counts() -> None:
    rec = Recorder(
        ok({"message": {"content": "pong"}, "prompt_eval_count": 31, "eval_count": 4, "done_reason": "stop"})
    )
    res = await OllamaProvider("http://ollama:11434/", transport=rec.transport).chat(
        ChatRequest("q", REQ.messages, temperature=0, max_tokens=8, json_schema=SCHEMA)
    )
    b = rec.body()
    assert str(rec.calls[0].url) == "http://ollama:11434/api/chat" and b["stream"] is False and b["format"] == SCHEMA
    assert (
        b["options"] == {"temperature": 0, "num_predict": 8}
        and (res.tokens_in, res.tokens_out) == (31, 4)
        and res.schema_enforced
    )


# ----------------------------------------------------------------------------------------------- errors
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(401, "auth_failed", False), (403, "auth_failed", False), (404, "not_found", False), (400, "bad_request", False),
     (429, "rate_limited", True), (500, "provider_error", True), (503, "provider_error", True)],
)  # fmt: skip
async def test_http_errors_map_to_codes_and_retryability(status: int, code: str, retryable: bool) -> None:
    rec = Recorder(lambda _r: httpx.Response(status, text="boom", headers={"retry-after": "2"}))
    with pytest.raises(LLMError) as e:
        await OpenAICompatProvider("openai", None, KEY, transport=rec.transport).chat(REQ)
    assert (e.value.code, e.value.retryable) == (code, retryable)
    if status == 429:
        assert e.value.retry_after == 2.0


async def test_an_error_body_that_echoes_the_key_is_redacted() -> None:
    rec = Recorder(lambda _r: httpx.Response(400, text=f"Invalid request for key {KEY} and Bearer {KEY}"))
    with pytest.raises(LLMError) as e:
        await OpenAICompatProvider("openai", None, KEY, transport=rec.transport).chat(REQ)
    assert KEY not in e.value.message and KEY not in repr(e.value)


async def test_timeouts_and_connection_failures_are_retryable_and_explained() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t", request=r)

    with pytest.raises(LLMError) as e:
        await OpenAICompatProvider("openai", None, KEY, transport=Recorder(boom).transport).chat(REQ)
    assert e.value.code == "timeout" and e.value.retryable

    def refused(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no", request=r)

    with pytest.raises(LLMError) as e2:
        await OllamaProvider("http://nowhere:1", transport=Recorder(refused).transport).chat(REQ)
    assert e2.value.code == "unreachable" and "running" in e2.value.message


async def test_non_json_and_empty_choices_are_bad_responses() -> None:
    with pytest.raises(LLMError) as e:
        await OpenAICompatProvider(
            "openai", None, KEY, transport=Recorder(lambda _r: httpx.Response(200, text="<html>")).transport
        ).chat(REQ)
    assert e.value.code == "bad_response"
    with pytest.raises(LLMError) as e2:
        await OpenAICompatProvider("openai", None, KEY, transport=Recorder(ok({"choices": []})).transport).chat(REQ)
    assert e2.value.code == "bad_response"


def test_redact_removes_known_and_pattern_secrets() -> None:
    out = redact(f"x {KEY} y api_key=abcdefghijklmnop12345", [KEY])
    assert KEY not in out and "abcdefghijklmnop12345" not in out


def test_factory_builds_the_right_adapter() -> None:
    kinds = {
        k: type(build_provider(k, None, "k", 10)).__name__
        for k in ["ollama", "openai", "openrouter", "groq", "anthropic", "gemini", "openai_compat"]
    }
    assert kinds == {"ollama": "OllamaProvider", "openai": "OpenAICompatProvider", "openrouter": "OpenAICompatProvider", "groq": "OpenAICompatProvider",
                     "anthropic": "AnthropicProvider", "gemini": "GeminiProvider", "openai_compat": "OpenAICompatProvider"}  # fmt: skip
