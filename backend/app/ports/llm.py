"""LLM provider port. Services depend on this, never on a vendor SDK (SPEC §5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class LLMError(Exception):
    """A model call failed. `message` is safe to show to users and never contains credentials."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.2
    max_tokens: int | None = None
    json_schema: dict[str, Any] | None = None  # ask the provider for schema-constrained output when it can
    # Context window to ask the server for. Local servers (Ollama) otherwise use their own small default and silently
    # drop the start of a long prompt. Hosted APIs ignore it.
    num_ctx: int | None = None


@dataclass
class ChatResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str | None = None
    schema_enforced: bool = False  # True when the provider itself constrained the output to the schema


@dataclass
class ModelInfo:
    model_id: str
    display_name: str | None = None
    context_length: int | None = None
    supports_json_schema: bool = False
    is_free: bool = False
    input_per_mtok: Decimal | None = None
    output_per_mtok: Decimal | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    kind: str
    native_schema: bool

    async def chat(self, req: ChatRequest) -> ChatResult: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def check(self) -> str:
        """Cheap reachability + credential check. Returns a short human message or raises LLMError."""
