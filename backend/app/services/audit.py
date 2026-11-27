from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog
from app.logging_setup import request_id_var


def record(
    session: AsyncSession,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    actor_kind: str = "user",
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Add an audit row to the current unit of work (caller commits)."""
    session.add(
        AuditLog(
            actor_id=actor_id,
            actor_kind=actor_kind,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip=ip,
            request_id=request_id_var.get(),
        )
    )
