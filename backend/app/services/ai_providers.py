"""Provider management: CRUD, encrypted credentials, connection tests, model discovery."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.factory import build_provider
from app.db.models import AiModel, AiProvider, EncryptedCredential, User
from app.db.models.ai import PROVIDER_KINDS
from app.errors import AppError, conflict, not_found
from app.ports.llm import LLMError, LLMProvider
from app.security.crypto import Vault, mask
from app.services import audit

CLOUD_KINDS = {"openai", "openrouter", "groq", "anthropic", "gemini"}
NEEDS_URL = {"ollama", "openai_compat"}


def _aad(provider_id: uuid.UUID) -> bytes:
    return f"ai-credential|{provider_id}".encode()


def _check_url(url: str | None, kind: str) -> str | None:
    if not url:
        if kind in NEEDS_URL:
            raise AppError("base_url_required", "This kind of provider needs a base URL.", 400)
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise AppError("invalid_url", "The base URL must be http(s) and must not contain credentials.", 400)
    return url.strip().rstrip("/")


async def get_provider(session: AsyncSession, provider_id: uuid.UUID) -> AiProvider:
    p = await session.get(AiProvider, provider_id)
    if p is None:
        raise not_found("Provider")
    return p


@dataclass
class ProviderView:
    provider: AiProvider
    credential_mask: str | None
    model_count: int


async def list_providers(session: AsyncSession) -> list[ProviderView]:
    counts = dict(
        (await session.execute(select(AiModel.provider_id, func.count()).group_by(AiModel.provider_id))).all()
    )
    masks = dict((await session.execute(select(EncryptedCredential.provider_id, EncryptedCredential.mask))).all())
    rows = (await session.execute(select(AiProvider).order_by(AiProvider.created_at, AiProvider.id))).scalars()
    return [ProviderView(p, masks.get(p.id), int(counts.get(p.id, 0))) for p in rows]


async def view(session: AsyncSession, p: AiProvider) -> ProviderView:
    m = (
        await session.execute(select(EncryptedCredential.mask).where(EncryptedCredential.provider_id == p.id))
    ).scalar_one_or_none()
    n = (
        await session.execute(select(func.count()).select_from(AiModel).where(AiModel.provider_id == p.id))
    ).scalar_one()
    return ProviderView(p, m, int(n))


async def create_provider(
    session: AsyncSession,
    vault: Vault,
    actor: User,
    *,
    kind: str,
    name: str,
    base_url: str | None,
    is_local: bool | None,
    rate_limit_rpm: int | None,
    timeout_s: int,
    api_key: str | None,
    ip: str | None,
) -> AiProvider:
    if kind not in PROVIDER_KINDS:
        raise AppError("invalid_kind", f"Unknown provider kind: {kind}", 400)
    local = kind == "ollama" if is_local is None else is_local
    if local and kind in CLOUD_KINDS:
        raise AppError("invalid_locality", "Hosted services cannot be marked local.", 400)
    if (
        await session.execute(select(AiProvider.id).where(func.lower(AiProvider.name) == name.strip().lower()))
    ).first():
        raise conflict("name_taken", "A provider with this name already exists.")
    p = AiProvider(
        kind=kind,
        name=name.strip(),
        base_url=_check_url(base_url, kind),
        is_local=local,
        rate_limit_rpm=rate_limit_rpm,
        timeout_s=timeout_s,
        enabled=True,
    )
    session.add(p)
    await session.flush()
    if api_key:
        _store_credential(session, vault, p, api_key)
    audit.record(
        session,
        "ai.provider_created",
        actor_id=actor.id,
        target_type="ai_provider",
        target_id=str(p.id),
        details={"kind": kind, "name": p.name, "is_local": local, "has_key": bool(api_key)},
        ip=ip,
    )
    await session.commit()
    return p


async def update_provider(
    session: AsyncSession,
    actor: User,
    p: AiProvider,
    *,
    name: str | None,
    base_url: str | None,
    enabled: bool | None,
    rate_limit_rpm: int | None,
    timeout_s: int | None,
    is_local: bool | None,
    set_rate_limit: bool,
    ip: str | None,
) -> AiProvider:
    if name is not None and name.strip().lower() != p.name.lower():
        if (
            await session.execute(select(AiProvider.id).where(func.lower(AiProvider.name) == name.strip().lower()))
        ).first():
            raise conflict("name_taken", "A provider with this name already exists.")
        p.name = name.strip()
    if base_url is not None:
        p.base_url = _check_url(base_url, p.kind)
    if enabled is not None:
        p.enabled = enabled
    if set_rate_limit:
        p.rate_limit_rpm = rate_limit_rpm
    if timeout_s is not None:
        p.timeout_s = timeout_s
    if is_local is not None:
        if is_local and p.kind in CLOUD_KINDS:
            raise AppError("invalid_locality", "Hosted services cannot be marked local.", 400)
        p.is_local = is_local
    audit.record(
        session, "ai.provider_updated", actor_id=actor.id, target_type="ai_provider", target_id=str(p.id), ip=ip
    )
    await session.commit()
    return p


async def delete_provider(session: AsyncSession, actor: User, p: AiProvider, ip: str | None) -> None:
    audit.record(
        session,
        "ai.provider_deleted",
        actor_id=actor.id,
        target_type="ai_provider",
        target_id=str(p.id),
        details={"name": p.name, "kind": p.kind},
        ip=ip,
    )
    await session.delete(p)  # credentials, models, prices and routes cascade; usage history keeps copied names
    await session.commit()


def _store_credential(session: AsyncSession, vault: Vault, p: AiProvider, api_key: str) -> None:
    sealed = vault.encrypt(api_key.strip(), _aad(p.id))
    session.add(
        EncryptedCredential(
            provider_id=p.id,
            ciphertext=sealed.ciphertext,
            wrapped_dek=sealed.wrapped_dek,
            key_version=sealed.key_version,
            mask=mask(api_key),
        )
    )


async def set_credential(
    session: AsyncSession, vault: Vault, actor: User, p: AiProvider, api_key: str, ip: str | None
) -> None:
    existing = (
        await session.execute(select(EncryptedCredential).where(EncryptedCredential.provider_id == p.id))
    ).scalar_one_or_none()
    if existing:
        await session.delete(existing)
        await session.flush()
    _store_credential(session, vault, p, api_key)
    audit.record(session, "ai.credential_set", actor_id=actor.id, target_type="ai_provider", target_id=str(p.id), ip=ip)
    await session.commit()


async def clear_credential(session: AsyncSession, actor: User, p: AiProvider, ip: str | None) -> None:
    existing = (
        await session.execute(select(EncryptedCredential).where(EncryptedCredential.provider_id == p.id))
    ).scalar_one_or_none()
    if existing:
        await session.delete(existing)
    audit.record(
        session, "ai.credential_removed", actor_id=actor.id, target_type="ai_provider", target_id=str(p.id), ip=ip
    )
    await session.commit()


async def api_key_for(session: AsyncSession, vault: Vault, p: AiProvider) -> str | None:
    c = (
        await session.execute(select(EncryptedCredential).where(EncryptedCredential.provider_id == p.id))
    ).scalar_one_or_none()
    if c is None:
        return None
    from app.security.crypto import Sealed

    return vault.decrypt(Sealed(c.ciphertext, c.wrapped_dek, c.key_version), _aad(p.id))


async def adapter_for(
    session: AsyncSession, vault: Vault, p: AiProvider, transport: httpx.AsyncBaseTransport | None = None
) -> LLMProvider:
    key = await api_key_for(session, vault, p)
    if p.kind in CLOUD_KINDS and not key:
        raise LLMError("no_credential", f"No API key is saved for '{p.name}'.")
    return build_provider(p.kind, p.base_url, key, float(p.timeout_s), transport)


@dataclass
class TestOutcome:
    ok: bool
    message: str
    latency_ms: int


async def test_provider(
    session: AsyncSession,
    vault: Vault,
    actor: User,
    p: AiProvider,
    ip: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TestOutcome:
    t0 = time.monotonic()
    try:
        msg = await (await adapter_for(session, vault, p, transport)).check()
        out = TestOutcome(True, msg, int((time.monotonic() - t0) * 1000))
    except LLMError as exc:
        out = TestOutcome(False, exc.message, int((time.monotonic() - t0) * 1000))
    p.last_test_at, p.last_test_ok, p.last_test_message = datetime.now(UTC), out.ok, out.message[:500]
    audit.record(
        session,
        "ai.provider_tested",
        actor_id=actor.id,
        target_type="ai_provider",
        target_id=str(p.id),
        details={"ok": out.ok},
        ip=ip,
    )
    await session.commit()
    return out


async def discover_models(
    session: AsyncSession,
    vault: Vault,
    actor: User,
    p: AiProvider,
    ip: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, int]:
    """Fetch the provider's model list and upsert it. Returns (added, updated).

    Models the provider no longer lists are kept, because routes may still point at them.
    """
    try:
        infos = await (await adapter_for(session, vault, p, transport)).list_models()
    except LLMError as exc:
        raise AppError("discovery_failed", exc.message, 502) from exc
    existing = {
        m.model_id: m for m in (await session.execute(select(AiModel).where(AiModel.provider_id == p.id))).scalars()
    }
    added = updated = 0
    now = datetime.now(UTC)
    for info in infos:
        m = existing.get(info.model_id)
        if m is None:
            m = AiModel(provider_id=p.id, model_id=info.model_id, enabled=True)
            session.add(m)
            added += 1
        else:
            updated += 1
        m.display_name, m.context_length = info.display_name, info.context_length
        m.supports_json_schema, m.is_free = info.supports_json_schema, info.is_free
        m.provider_input_per_mtok, m.provider_output_per_mtok = info.input_per_mtok, info.output_per_mtok
        m.discovered_at = now
    audit.record(
        session,
        "ai.models_discovered",
        actor_id=actor.id,
        target_type="ai_provider",
        target_id=str(p.id),
        details={"added": added, "updated": updated},
        ip=ip,
    )
    await session.commit()
    return added, updated
