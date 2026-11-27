from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.sources.youtube_url import (
    NotSupportedLinkError,
    is_youtube_host,
    normalize_video_url,
)
from app.config import Settings
from app.db.models import JobStage, MediaAsset, ProcessingJob, SourcePermission, User, VideoSource
from app.errors import AppError, conflict, not_found
from app.ports.storage import StorageBackend
from app.security.ssrf import UnsafeUrlError, check_url
from app.services import audit, ingest_settings, jobs
from app.services.access import Need, require_project

log = logging.getLogger(__name__)
ATTESTATION = "I own this content or have permission to use it."


async def _new_source(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    origin_url: str | None,
    filename: str | None,
    ip: str | None,
) -> VideoSource:
    src = VideoSource(
        project_id=project_id,
        kind=kind,
        title=title[:500],
        origin_url=origin_url,
        original_filename=filename,
        status="pending",
        created_by=user.id,
    )
    session.add(src)
    await session.flush()
    session.add(
        SourcePermission(
            source_id=src.id,
            user_id=user.id,
            attested_at=datetime.now(UTC),
            statement_text=ATTESTATION,
        )
    )
    audit.record(
        session,
        "source.created",
        actor_id=user.id,
        target_type="source",
        target_id=str(src.id),
        details={"kind": kind, "project_id": str(project_id)},
        ip=ip,
    )
    return src


async def create_from_link(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    user: User,
    project_id: uuid.UUID,
    url: str,
    attested: bool,
    ip: str | None,
) -> tuple[VideoSource, ProcessingJob]:
    await require_project(session, user, project_id, Need.WRITE)
    if not attested:  # SPEC §13.8: no link ingestion without the per-source attestation
        raise AppError(
            "attestation_required",
            "Confirm that you own this content or have permission to use it",
            400,
        )
    url = url.strip()
    if is_youtube_host(url):
        state = await ingest_settings.get_state(session, redis)
        if not state.enabled:
            raise AppError(
                "ingest_disabled",
                "Link downloads are turned off. An admin can enable them in Settings.",
                403,
            )
        if not state.worker_running:
            raise AppError(
                "ingest_unavailable",
                "The download service is not running. Start it with the 'ingest' profile.",
                503,
            )
        if await ingest_settings.downloads_today(session) >= state.daily_limit:
            raise AppError("daily_limit_reached", f"Daily download limit of {state.daily_limit} reached", 429)
        try:
            norm = normalize_video_url(url)
        except NotSupportedLinkError as exc:
            raise AppError(exc.code, exc.message, 400) from exc
        src = await _new_source(
            session,
            user,
            project_id,
            kind="ytdlp",
            title=norm,
            origin_url=norm,
            filename=None,
            ip=ip,
        )
    else:
        try:  # reject obviously unsafe targets immediately; the worker re-checks on every hop
            check_url(url, allow_private=settings.direct_url_allow_private)
        except UnsafeUrlError as exc:
            raise AppError(exc.code, exc.message, 400) from exc
        host = urlsplit(url).hostname or "link"
        src = await _new_source(
            session,
            user,
            project_id,
            kind="direct_url",
            title=f"{host}{urlsplit(url).path}"[:200],
            origin_url=url,
            filename=None,
            ip=ip,
        )
    job = await jobs.create_analyze_job(session, src, user.id)
    await session.commit()
    jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), src.kind)
    return src, job


async def create_from_staged_upload(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    *,
    filename: str,
    title: str,
    asset: MediaAsset,
    ip: str | None,
) -> tuple[VideoSource, ProcessingJob]:
    session.add(asset)
    await session.flush()
    src = await _new_source(
        session,
        user,
        project_id,
        kind="upload",
        title=title,
        origin_url=None,
        filename=filename,
        ip=ip,
    )
    src.original_asset_id, src.size_bytes = asset.id, asset.size_bytes
    job = await jobs.create_analyze_job(session, src, user.id)
    await session.flush()
    return src, job


async def get_source(session: AsyncSession, user: User, source_id: uuid.UUID, need: Need = Need.READ) -> VideoSource:
    src = await session.get(VideoSource, source_id)
    if src is None:
        raise not_found("Source")
    await require_project(session, user, src.project_id, need)
    return src


async def list_sources(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    *,
    cursor: uuid.UUID | None,
    limit: int,
) -> tuple[list[VideoSource], uuid.UUID | None]:
    await require_project(session, user, project_id, Need.READ)
    q = select(VideoSource).where(VideoSource.project_id == project_id).order_by(VideoSource.id.desc()).limit(limit + 1)
    if cursor:
        q = q.where(VideoSource.id < cursor)
    rows = list((await session.execute(q)).scalars())
    return rows[:limit], (rows[limit - 1].id if len(rows) > limit else None)


async def latest_job(session: AsyncSession, source_id: uuid.UUID) -> ProcessingJob | None:
    return (
        await session.execute(
            select(ProcessingJob).where(ProcessingJob.source_id == source_id).order_by(ProcessingJob.id.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def reprocess(session: AsyncSession, user: User, source_id: uuid.UUID, ip: str | None) -> ProcessingJob:
    src = await get_source(session, user, source_id, Need.WRITE)
    if await jobs.active_job_exists(session, source_id=source_id):
        raise conflict("job_active", "This source is already being processed")
    job = await jobs.create_analyze_job(session, src, user.id)
    src.status, src.error_code, src.error_message = "pending", None, None
    audit.record(
        session,
        "source.reprocess",
        actor_id=user.id,
        target_type="source",
        target_id=str(src.id),
        ip=ip,
    )
    await session.commit()
    jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), src.kind)
    return job


async def delete_source(
    session: AsyncSession, storage: StorageBackend, user: User, source_id: uuid.UUID, ip: str | None
) -> None:
    src = await get_source(session, user, source_id, Need.WRITE)
    if await jobs.active_job_exists(session, source_id=source_id):
        raise conflict("job_active", "Processing is still running for this source. Cancel it first.")
    ids = {
        i
        for i in (
            src.original_asset_id,
            src.proxy_asset_id,
            src.waveform_asset_id,
            src.thumbnail_asset_id,
        )
        if i
    }
    # Older results (e.g. a proxy replaced by a re-run with new settings) are only referenced by job stages.
    stage_assets = (
        await session.execute(
            select(JobStage.output_asset_id)
            .join(ProcessingJob, ProcessingJob.id == JobStage.job_id)
            .where(ProcessingJob.source_id == src.id, JobStage.output_asset_id.is_not(None))
        )
    ).scalars()
    ids |= {i for i in stage_assets if i}
    assets = list((await session.execute(select(MediaAsset).where(MediaAsset.id.in_(ids)))).scalars()) if ids else []
    audit.record(
        session,
        "source.deleted",
        actor_id=user.id,
        target_type="source",
        target_id=str(src.id),
        details={"title": src.title, "assets": len(assets)},
        ip=ip,
    )
    keys = [a.storage_key for a in assets]
    for a in assets:
        await session.delete(a)
    await session.delete(src)
    await session.commit()
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            log.exception("could not delete stored object", extra={"extra_fields": {"key": key}})
