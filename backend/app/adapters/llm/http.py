"""Shared HTTP behaviour for provider adapters: timeouts, error mapping, secret redaction."""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.ports.llm import LLMError

MAX_BODY_SNIPPET = 240


def client(timeout_s: float, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=10.0),
        transport=transport,
        follow_redirects=False,
    )


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[redacted]")
    text = re.sub(r"(?i)(bearer|api[-_ ]?key|x-api-key)[\"':= ]+[A-Za-z0-9._\-]{12,}", r"\1 [redacted]", text)
    return text


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if raw and raw.replace(".", "", 1).isdigit():
        return min(float(raw), 30.0)
    return None


def raise_for_status(resp: httpx.Response, secrets: list[str]) -> None:
    if resp.status_code < 400:
        return
    try:
        body = resp.text
    except Exception:
        body = ""
    detail = redact(" ".join(body.split()), secrets)[:MAX_BODY_SNIPPET]
    s = resp.status_code
    if s in (401, 403):
        raise LLMError("auth_failed", "The provider rejected the credentials. Check the API key.", status=s)
    if s == 404:
        raise LLMError("not_found", f"Not found (the model or URL may be wrong). {detail}".strip(), status=s)
    if s == 408 or s == 429 or s >= 500:
        code = "rate_limited" if s == 429 else "provider_error"
        raise LLMError(
            code,
            f"The provider answered {s}. {detail}".strip(),
            retryable=True,
            status=s,
            retry_after=_retry_after(resp),
        )
    raise LLMError("bad_request", f"The provider refused the request ({s}). {detail}".strip(), status=s)


def map_transport_error(exc: Exception) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        return LLMError("timeout", "The provider did not answer in time.", retryable=True)
    if isinstance(exc, httpx.ConnectError):
        return LLMError(
            "unreachable", "Could not connect to the provider. Is it running and is the URL right?", retryable=True
        )
    return LLMError("network_error", f"Network error talking to the provider ({type(exc).__name__}).", retryable=True)


def json_or_error(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError("bad_response", "The provider returned something that is not JSON.", retryable=True) from exc
    if not isinstance(data, dict):
        raise LLMError("bad_response", "The provider returned an unexpected response.", retryable=True)
    return data
