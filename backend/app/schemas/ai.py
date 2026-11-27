from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["ollama", "openai", "openrouter", "groq", "anthropic", "gemini", "openai_compat"]
Task = Literal["highlight", "metadata", "translation", "captions_cleanup"]


class ProviderIn(BaseModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    is_local: bool | None = None
    rate_limit_rpm: int | None = Field(default=None, ge=1, le=100_000)
    timeout_s: int = Field(default=120, ge=5, le=1800)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    is_local: bool | None = None
    rate_limit_rpm: int | None = Field(default=None, ge=1, le=100_000)
    timeout_s: int | None = Field(default=None, ge=5, le=1800)


class CredentialIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=500)


class ProviderOut(BaseModel):
    """Never carries a secret. `credential_mask` is the only trace of a saved key."""

    id: uuid.UUID
    kind: Kind
    name: str
    base_url: str | None
    enabled: bool
    is_local: bool
    rate_limit_rpm: int | None
    timeout_s: int
    has_credential: bool
    credential_mask: str | None
    model_count: int
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None


class TestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: int


class DiscoverOut(BaseModel):
    added: int
    updated: int


class PullIn(BaseModel):
    model: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/\-]+$")


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: uuid.UUID
    provider_name: str
    model_id: str
    display_name: str | None
    context_length: int | None
    supports_json_schema: bool
    is_free: bool
    enabled: bool
    is_local: bool
    input_per_mtok: Decimal | None  # effective price the cost estimate uses
    output_per_mtok: Decimal | None
    price_source: Literal["local", "user", "provider", "free", "unknown"]


class ModelPatch(BaseModel):
    enabled: bool


class PriceIn(BaseModel):
    input_per_mtok: Decimal = Field(ge=0, le=100_000)
    output_per_mtok: Decimal = Field(ge=0, le=100_000)


class ModeIn(BaseModel):
    mode: Literal["LOCAL", "REMOTE", "HYBRID"]


class RoutesIn(BaseModel):
    routes: dict[Task, list[uuid.UUID]]


class TestPromptIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    model_id: uuid.UUID | None = None
    task: Task = "highlight"
    json_mode: bool = False
    max_tokens: int | None = Field(default=256, ge=1, le=8192)


class TestPromptOut(BaseModel):
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_est: Decimal | None
    latency_ms: int
    repairs: int
    failed_attempts: list[dict[str, str]]


class UsageRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    provider_name: str
    model_name: str
    task: str
    tokens_in: int
    tokens_out: int
    cost_est: Decimal | None
    latency_ms: int
    status: str
    error_code: str | None
    error_message: str | None
    repairs: int
    attempt: int
