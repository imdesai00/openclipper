"""Mode (LOCAL / REMOTE / HYBRID), per-task routes, price overrides, usage reports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AiModel, AiProvider, AiUsageLog, PriceEntry, Setting, TaskRoute, User
from app.db.models.ai import TASKS
from app.errors import AppError, not_found
from app.services import audit
from app.services.llm import MODES, get_mode, mode_allows


async def set_mode(session: AsyncSession, actor: User, mode: str, ip: str | None) -> str:
    if mode not in MODES:
        raise AppError("invalid_mode", f"Mode must be one of {', '.join(MODES)}.", 400)
    row = (
        await session.execute(select(Setting).where(Setting.scope == "global", Setting.key == "ai").with_for_update())
    ).scalar_one_or_none()
    if row is None:
        session.add(Setting(scope="global", key="ai", value={"mode": mode}))
    else:
        row.value = {**(row.value or {}), "mode": mode}
    audit.record(session, "ai.mode_changed", actor_id=actor.id, details={"mode": mode}, ip=ip)
    await session.commit()
    return mode


async def routes(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    mode = await get_mode(session)
    rows = (
        await session.execute(
            select(TaskRoute, AiModel, AiProvider)
            .join(AiModel, AiModel.id == TaskRoute.model_id)
            .join(AiProvider, AiProvider.id == AiModel.provider_id)
            .order_by(TaskRoute.task, TaskRoute.position)
        )
    ).all()
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in TASKS}
    for r, m, p in rows:
        reason = None
        if not p.enabled:
            reason = "The provider is switched off."
        elif not m.enabled:
            reason = "The model is switched off."
        elif not mode_allows(mode, p.is_local):
            reason = f"{mode.title()} mode does not allow {'local' if p.is_local else 'remote'} providers."
        out[r.task].append(
            {
                "model_id": m.id,
                "model_name": m.model_id,
                "provider_id": p.id,
                "provider_name": p.name,
                "is_local": p.is_local,
                "usable": reason is None,
                "unusable_reason": reason,
            }
        )
    return out


async def set_routes(
    session: AsyncSession, actor: User, task_routes: dict[str, list[uuid.UUID]], ip: str | None
) -> None:
    for task, ids in task_routes.items():
        if task not in TASKS:
            raise AppError("invalid_task", f"Unknown task: {task}", 400)
        if len(set(ids)) != len(ids):
            raise AppError("duplicate_model", "A model can only appear once per task.", 400)
        if ids:
            found = set((await session.execute(select(AiModel.id).where(AiModel.id.in_(ids)))).scalars())
            if found != set(ids):
                raise not_found("Model")
        await session.execute(delete(TaskRoute).where(TaskRoute.task == task))
        await session.flush()
        for pos, mid in enumerate(ids):
            session.add(TaskRoute(task=task, position=pos, model_id=mid))
    audit.record(
        session,
        "ai.routes_changed",
        actor_id=actor.id,
        details={t: [str(i) for i in ids] for t, ids in task_routes.items()},
        ip=ip,
    )
    await session.commit()


async def set_price(
    session: AsyncSession,
    actor: User,
    model: AiModel,
    input_per_mtok: Decimal,
    output_per_mtok: Decimal,
    ip: str | None,
) -> PriceEntry:
    e = (await session.execute(select(PriceEntry).where(PriceEntry.model_id == model.id))).scalar_one_or_none()
    if e is None:
        e = PriceEntry(model_id=model.id, input_per_mtok=input_per_mtok, output_per_mtok=output_per_mtok)
        session.add(e)
    else:
        e.input_per_mtok, e.output_per_mtok = input_per_mtok, output_per_mtok
    audit.record(session, "ai.price_set", actor_id=actor.id, target_type="ai_model", target_id=str(model.id), ip=ip)
    await session.commit()
    return e


async def clear_price(session: AsyncSession, actor: User, model: AiModel, ip: str | None) -> None:
    await session.execute(delete(PriceEntry).where(PriceEntry.model_id == model.id))
    audit.record(session, "ai.price_cleared", actor_id=actor.id, target_type="ai_model", target_id=str(model.id), ip=ip)
    await session.commit()


async def usage_report(session: AsyncSession, days: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    base = AiUsageLog.created_at >= since
    totals = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(AiUsageLog.status == "error"),
                func.coalesce(func.sum(AiUsageLog.tokens_in), 0),
                func.coalesce(func.sum(AiUsageLog.tokens_out), 0),
                func.coalesce(func.sum(AiUsageLog.cost_est), 0),
                func.count().filter((AiUsageLog.cost_est.is_(None)) & (AiUsageLog.status == "ok")),
            ).where(base)
        )
    ).one()
    by_model = (
        await session.execute(
            select(
                AiUsageLog.provider_name,
                AiUsageLog.model_name,
                func.count(),
                func.count().filter(AiUsageLog.status == "error"),
                func.coalesce(func.sum(AiUsageLog.tokens_in), 0),
                func.coalesce(func.sum(AiUsageLog.tokens_out), 0),
                func.coalesce(func.sum(AiUsageLog.cost_est), 0),
                func.count().filter((AiUsageLog.cost_est.is_(None)) & (AiUsageLog.status == "ok")),
            )
            .where(base)
            .group_by(AiUsageLog.provider_name, AiUsageLog.model_name)
            .order_by(func.count().desc())
        )
    ).all()
    recent = (
        (await session.execute(select(AiUsageLog).where(base).order_by(AiUsageLog.id.desc()).limit(50))).scalars().all()
    )
    return {
        "days": days,
        "calls": totals[0],
        "errors": totals[1],
        "tokens_in": int(totals[2]),
        "tokens_out": int(totals[3]),
        "cost_est": totals[4],
        "calls_with_unknown_cost": totals[5],
        "by_model": [
            {
                "provider": r[0],
                "model": r[1],
                "calls": r[2],
                "errors": r[3],
                "tokens_in": int(r[4]),
                "tokens_out": int(r[5]),
                "cost_est": r[6],
                "calls_with_unknown_cost": r[7],
            }
            for r in by_model
        ],
        "recent": recent,
    }
