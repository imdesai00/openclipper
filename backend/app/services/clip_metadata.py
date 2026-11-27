"""Per-platform clip metadata: read, edit by hand, generate with a model. Generation is a job in the llm worker."""

from __future__ import annotations

import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clip, ClipMetadata, ClipVersion, ProcessingJob, TaskRoute, User, VideoSource
from app.errors import AppError, conflict, not_found
from app.ids import uuid7
from app.pipeline.registry import GENERATE_METADATA
from app.pipeline.types import MetadataData
from app.services import audit, jobs
from app.services import clip_metadata_logic as ml
from app.services import highlights as hl_svc
from app.services.access import Need, require_project

TASK = "metadata"
EMPTY: dict[str, Any] = {k: ("" if k not in ("hashtags", "tags") else []) for k in ml.FIELDS}


async def _clip(session: AsyncSession, user: User, clip_id: uuid.UUID, need: Need) -> Clip:
    clip = await session.get(Clip, clip_id)
    if clip is None:
        raise not_found("Clip")
    await require_project(session, user, clip.project_id, need)
    return clip


def row_fields(r: ClipMetadata) -> dict[str, Any]:
    return {k: getattr(r, k) for k in ml.FIELDS} | {
        "platform": r.platform,
        "edited": r.edited,
        "generated_by": r.generated_by,
        "last_error": r.last_error,
        "updated_at": r.updated_at,
    }


async def get_all(session: AsyncSession, user: User, clip_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    await _clip(session, user, clip_id, Need.READ)
    rows = {
        r.platform: r
        for r in (await session.execute(select(ClipMetadata).where(ClipMetadata.clip_id == clip_id))).scalars()
    }
    out: dict[str, dict[str, Any]] = {}
    for p in ml.PLATFORMS:
        f = (
            row_fields(rows[p])
            if p in rows
            else {**EMPTY, "platform": p, "edited": False, "generated_by": None, "last_error": None, "updated_at": None}
        )
        errors, warnings = ml.validate(p, f)
        out[p] = {**f, "errors": errors, "warnings": warnings}
    return out


async def update(
    session: AsyncSession, user: User, clip_id: uuid.UUID, platform: str, body: dict[str, Any], ip: str | None
) -> dict[str, Any]:
    if platform not in ml.PLATFORMS:
        raise AppError("invalid_platform", "Platform must be generic, youtube or instagram.", 400)
    await _clip(session, user, clip_id, Need.WRITE)
    row = (
        await session.execute(
            select(ClipMetadata)
            .where(ClipMetadata.clip_id == clip_id, ClipMetadata.platform == platform)
            .with_for_update()
        )
    ).scalar_one_or_none()
    cur = row_fields(row) if row else dict(EMPTY)
    merged = {k: body[k] for k in ml.FIELDS if body.get(k) is not None} | {
        k: cur[k] for k in ml.FIELDS if body.get(k) is None
    }
    merged["hashtags"] = ml.normalize_hashtags(list(merged["hashtags"]))
    merged["tags"] = ml.normalize_tags(list(merged["tags"]))
    errors, warnings = ml.validate(platform, merged)
    if errors:  # a hard limit: refuse, naming each one, and change nothing
        raise AppError("limit_exceeded", " ".join(errors), 400, details=errors)
    if row is None:
        row = ClipMetadata(id=uuid7(), clip_id=clip_id, platform=platform)
        session.add(row)
    for k in ml.FIELDS:
        setattr(row, k, merged[k])
    row.edited, row.updated_by = True, user.id
    audit.record(
        session,
        "clip.metadata_edited",
        actor_id=user.id,
        target_type="clip",
        target_id=str(clip_id),
        details={"platform": platform},
        ip=ip,
    )
    await session.commit()
    await session.refresh(row)  # updated_at is set by the database, so it must be loaded again after the commit
    return {**row_fields(row), "errors": [], "warnings": warnings}


async def clip_text(session: AsyncSession, clip: Clip) -> tuple[str, VideoSource]:
    v = (
        await session.execute(
            select(ClipVersion).where(ClipVersion.clip_id == clip.id, ClipVersion.version == clip.current_version)
        )
    ).scalar_one()
    src = await session.get(VideoSource, clip.source_id)
    assert src is not None
    if v.transcript_id is None:
        raise AppError(
            "no_transcript", "Transcribe the video first; the text is written from what is said in the clip.", 409
        )
    t = await hl_svc.load_view(session, v.transcript_id)
    words = [w.text for w in t.words if w.start_ms >= v.start_ms - 40 and w.end_ms <= v.end_ms + 40]
    if not words:
        raise AppError(
            "no_speech", "Nothing is said in this part of the video, so there is nothing to write about.", 409
        )
    return " ".join(words), src


async def request_generation(
    session: AsyncSession,
    redis: Redis,
    user: User,
    clip_id: uuid.UUID,
    platforms: list[str],
    overwrite: bool,
    ip: str | None,
) -> ProcessingJob:
    clip = await _clip(session, user, clip_id, Need.WRITE)
    plats = [p for p in dict.fromkeys(platforms) if p in ml.PLATFORMS] or list(ml.PLATFORMS)
    if (await session.execute(select(TaskRoute.model_id).where(TaskRoute.task == TASK).limit(1))).first() is None:
        raise AppError("no_model_configured", "Choose a model for writing metadata in the AI settings first.", 409)
    text, src = await clip_text(session, clip)
    if not overwrite:  # never throw away text a person wrote
        edited = list(
            (
                await session.execute(
                    select(ClipMetadata.platform).where(
                        ClipMetadata.clip_id == clip.id, ClipMetadata.platform.in_(plats), ClipMetadata.edited.is_(True)
                    )
                )
            ).scalars()
        )
        if edited:
            raise AppError(
                "edited_metadata",
                f"You edited the {', '.join(edited)} text. Generating again would replace it; confirm to overwrite.",
                409,
                details=edited,
            )
    if not await redis.exists(hl_svc.LLM_HEARTBEAT):
        raise AppError("llm_unavailable", "The highlight and metadata worker is not running. Start 'worker-llm'.", 503)
    active = (
        await session.execute(
            select(ProcessingJob.id)
            .where(
                ProcessingJob.kind == GENERATE_METADATA,
                ProcessingJob.status.in_(("queued", "running")),
                ProcessingJob.params["clip_id"].astext == str(clip.id),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise conflict("job_active", "Metadata is already being written for this clip.")
    params: dict[str, object] = {
        "clip_id": str(clip.id),
        "platforms": plats,
        "overwrite": overwrite,
        "text": text,
        "clip_title": clip.title,
        "source_title": src.title,
        "user_id": str(user.id),
        "nonce": uuid.uuid4().hex,  # an explicit request always asks the model again
    }
    job = await jobs.create_job(session, src, user.id, kind=GENERATE_METADATA, params=params)
    audit.record(
        session,
        "clip.metadata_requested",
        actor_id=user.id,
        target_type="clip",
        target_id=str(clip.id),
        details={"platforms": plats},
        ip=ip,
    )
    await session.commit()
    jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), src.kind)
    return job


async def latest_job(session: AsyncSession, clip_id: uuid.UUID) -> ProcessingJob | None:
    return (
        await session.execute(
            select(ProcessingJob)
            .where(ProcessingJob.kind == GENERATE_METADATA, ProcessingJob.params["clip_id"].astext == str(clip_id))
            .order_by(ProcessingJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def persist_metadata(session: AsyncSession, *, data: MetadataData) -> None:
    """Called by the runner: store what the model wrote, never over a person's edits unless they asked for that."""
    clip_id = uuid.UUID(data.clip_id)
    if await session.get(Clip, clip_id) is None:
        return  # deleted while generating
    existing = {
        r.platform: r
        for r in (await session.execute(select(ClipMetadata).where(ClipMetadata.clip_id == clip_id))).scalars()
    }
    for platform, f in data.items.items():
        row = existing.get(platform)
        if row is not None and row.edited and not data.overwrite:
            continue
        if row is None:
            row = ClipMetadata(id=uuid7(), clip_id=clip_id, platform=platform)
            session.add(row)
        for k in ml.FIELDS:
            setattr(row, k, f[k])
        row.edited, row.generated_by, row.last_error = False, data.generated_by, None
    for platform, why in data.errors.items():  # a platform that failed says so, next to its (possibly empty) fields
        row = existing.get(platform)
        if row is None:
            row = ClipMetadata(id=uuid7(), clip_id=clip_id, platform=platform)
            session.add(row)
        row.last_error = why[:1000]
    await session.flush()
