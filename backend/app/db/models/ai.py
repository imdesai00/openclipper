from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

PROVIDER_KINDS = ("ollama", "openai", "openrouter", "groq", "anthropic", "gemini", "openai_compat")
TASKS = ("highlight", "metadata", "translation", "captions_cleanup")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} in ({', '.join(repr(v) for v in values)})"


class AiProvider(IdMixin, TimestampMixin, Base):
    __tablename__ = "ai_providers"
    __table_args__ = (CheckConstraint(_in("kind", PROVIDER_KINDS), name="ck_ai_providers_kind"),)

    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_local: Mapped[bool] = mapped_column(Boolean, nullable=False)  # decides what LOCAL / REMOTE mode allows
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="120")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_message: Mapped[str | None] = mapped_column(Text)


class EncryptedCredential(IdMixin, TimestampMixin, Base):
    __tablename__ = "encrypted_credentials"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(String(32), nullable=False)
    mask: Mapped[str] = mapped_column(String(32), nullable=False)  # the only form that is ever shown


class AiModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "ai_models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_ai_models_provider_model"),)

    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(300))
    context_length: Mapped[int | None] = mapped_column(Integer)
    supports_json_schema: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Prices the provider itself publishes (USD per million tokens). A user override lives in price_table.
    provider_input_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    provider_output_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceEntry(IdMixin, TimestampMixin, Base):
    """User-editable price override for a model (SPEC §6.I). Takes precedence over provider-published prices."""

    __tablename__ = "price_table"

    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    input_per_mtok: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    output_per_mtok: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")


class TaskRoute(IdMixin, TimestampMixin, Base):
    """Ordered fallback chain per task: position 0 is tried first."""

    __tablename__ = "task_model_routes"
    __table_args__ = (
        UniqueConstraint("task", "position", name="uq_task_routes_task_position"),
        CheckConstraint(_in("task", TASKS), name="ck_task_routes_task"),
    )

    task: Mapped[str] = mapped_column(String(30), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False, index=True
    )


class AiUsageLog(IdMixin, Base):
    """One row per model call attempt, successes and failures alike. Names are copied so history survives deletion."""

    __tablename__ = "ai_usage_logs"
    __table_args__ = (Index("ix_ai_usage_logs_created_at", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="SET NULL"), index=True
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_models.id", ondelete="SET NULL"), index=True)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    task: Mapped[str] = mapped_column(String(30), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_est: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))  # NULL = price unknown (never shown as $0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # ok | error
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    repairs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    request_id: Mapped[str | None] = mapped_column(String(64))
