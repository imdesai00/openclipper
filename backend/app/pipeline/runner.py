"""Stage runner: checkpointing, leases, heartbeats, progress events, failure handling (SPEC §5.4)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from redis import Redis as SyncRedis
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.adapters.storage.keys import new_key
from app.config import Settings, get_settings
from app.db.models import (
    AssetLease,
    JobStage,
    MediaAsset,
    ProcessingJob,
    VideoSource,
)
from app.db.session import make_engine, make_sessionmaker
from app.pipeline.registry import ANALYZE, STAGES
from app.pipeline.types import SourceInfo, StageContext, StageError, StageResult
from app.ports.storage import StorageBackend
from app.services import events
from app.services.storage import build_storage

log = logging.getLogger(__name__)
HEARTBEAT_S = 5
CANCEL_KEY = "job:cancel:{}"


def _now() -> datetime:
    return datetime.now(UTC)


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def request_cancel(redis: SyncRedis, job_id: uuid.UUID) -> None:
    redis.set(CANCEL_KEY.format(job_id), "1", ex=6 * 3600)


async def execute_stage(job_id: uuid.UUID, stage_name: str) -> None:
    """Entry point used by the Celery task for one stage."""
    settings = get_settings()
    engine = make_engine(settings.database_url, poolclass=NullPool)
    redis = SyncRedis.from_url(settings.redis_url)
    try:
        await _execute(make_sessionmaker(engine), build_storage(settings), redis, settings, job_id, stage_name)
    finally:
        redis.close()
        await engine.dispose()


async def _asset_key(session: AsyncSession, asset_id: uuid.UUID | None) -> str | None:
    if not asset_id:
        return None
    asset = await session.get(MediaAsset, asset_id)
    return asset.storage_key if asset and asset.deleted_at is None else None


async def _source_info(session: AsyncSession, source: VideoSource) -> SourceInfo:
    key = await _asset_key(session, source.original_asset_id)
    return SourceInfo(
        id=source.id,
        project_id=source.project_id,
        kind=source.kind,
        origin_url=source.origin_url,
        original_filename=source.original_filename,
        original_key=key,
        duration_s=source.duration_s,
        width=source.width,
        height=source.height,
        has_audio=source.has_audio,
        has_video=source.has_video,
        media_hash=source.media_hash,
        proxy_key=await _asset_key(session, source.proxy_asset_id),
        waveform_key=await _asset_key(session, source.waveform_asset_id),
    )


def _event(job: ProcessingJob, stage: JobStage, source: VideoSource, **extra: Any) -> dict[str, Any]:
    return {
        "type": "job.update",
        "job_id": str(job.id),
        "project_id": str(job.project_id),
        "source_id": str(source.id),
        "stage": stage.stage,
        "stage_status": stage.status,
        "progress": stage.progress,
        "message": stage.message,
        "job_status": job.status,
        "source_status": source.status,
        "job_kind": job.kind,
        **extra,
    }


async def _job_inputs(session: AsyncSession, job_id: uuid.UUID) -> dict[str, str]:
    """Storage keys of what earlier stages of THIS job produced, so a stage can read its predecessor's output."""
    rows = (
        await session.execute(
            select(JobStage.stage, MediaAsset.storage_key)
            .join(MediaAsset, MediaAsset.id == JobStage.output_asset_id)
            .where(JobStage.job_id == job_id, JobStage.status.in_(("succeeded", "skipped")))
        )
    ).all()
    return {stage: key for stage, key in rows}


async def _find_reusable(
    session: AsyncSession, source_id: uuid.UUID, stage: str, phash: str, storage: StorageBackend, exclude_job: uuid.UUID
) -> JobStage | None:
    rows = (
        await session.execute(
            select(JobStage)
            .join(ProcessingJob, ProcessingJob.id == JobStage.job_id)
            .where(
                ProcessingJob.source_id == source_id,
                JobStage.stage == stage,
                JobStage.params_hash == phash,
                JobStage.status == "succeeded",
                JobStage.job_id != exclude_job,
            )
            .order_by(JobStage.finished_at.desc())
        )
    ).scalars()
    for cand in rows:
        if cand.output_asset_id is None:
            return cand  # stage without a file output (validate): nothing to verify
        asset = await session.get(MediaAsset, cand.output_asset_id)
        if asset and asset.deleted_at is None and storage.stat(asset.storage_key):
            return cand
    return None


async def _heartbeat(
    sm: async_sessionmaker[AsyncSession], job_id: uuid.UUID, stage_id: uuid.UUID, state: dict[str, Any]
) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        async with sm() as s:
            await s.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(heartbeat_at=_now()))
            await s.execute(
                update(JobStage)
                .where(JobStage.id == stage_id)
                .values(progress=state["progress"], message=state["message"])
            )
            await s.commit()


async def _fail(
    sm: async_sessionmaker[AsyncSession],
    redis: SyncRedis,
    job_id: uuid.UUID,
    stage_id: uuid.UUID,
    code: str,
    message: str,
) -> None:
    cancelled = code == "cancelled"
    async with sm() as s:
        job = await s.get(ProcessingJob, job_id)
        stage = await s.get(JobStage, stage_id)
        assert job and stage and job.source_id
        source = await s.get(VideoSource, job.source_id)
        assert source
        now = _now()
        stage.status, stage.finished_at = ("cancelled" if cancelled else "failed"), now
        stage.error_code, stage.error_message = code, message[:2000]
        job.status = "cancelled" if cancelled else "failed"
        job.finished_at, job.error_code, job.error_message = now, code, message[:2000]
        if job.kind == ANALYZE:
            source.status = "failed"
            source.error_code, source.error_message = code, message[:2000]
        await s.commit()
        events.publish_sync(
            redis,
            job.project_id,
            _event(job, stage, source, error_code=code, error_message=message),
        )


async def _execute(
    sm: async_sessionmaker[AsyncSession],
    storage: StorageBackend,
    redis: SyncRedis,
    settings: Settings,
    job_id: uuid.UUID,
    stage_name: str,
) -> None:
    stage_def = STAGES[stage_name]
    async with sm() as s:
        job = await s.get(ProcessingJob, job_id)
        if job is None or job.status in ("failed", "cancelled", "succeeded") or job.source_id is None:
            return  # an earlier stage failed or the job was cancelled: later chain links do nothing
        stage = (
            await s.execute(select(JobStage).where(JobStage.job_id == job_id, JobStage.stage == stage_name))
        ).scalar_one()
        if stage.status in ("succeeded", "skipped"):
            return
        source = await s.get(VideoSource, job.source_id)
        assert source
        now = _now()
        analysis = job.kind == ANALYZE  # only analysis jobs describe the state of the source itself
        job_params = dict(job.params or {})
        phash = params_hash(stage_def.params(settings, job_params))
        job.status, job.heartbeat_at = "running", now
        job.started_at = job.started_at or now
        stage.status, stage.attempts, stage.progress, stage.message = (
            "running",
            stage.attempts + 1,
            0.0,
            None,
        )
        stage.started_at, stage.params_hash = now, phash
        stage.error_code = stage.error_message = None
        if analysis:
            source.status, source.error_code, source.error_message = "processing", None, None
        await s.commit()
        events.publish_sync(redis, job.project_id, _event(job, stage, source))

        reuse = await _find_reusable(s, source.id, stage_name, phash, storage, job_id)
        if reuse is not None:  # checkpoint hit: same source, stage and parameters already done
            stage.status, stage.progress, stage.finished_at = "skipped", 1.0, _now()
            stage.output_asset_id, stage.reused_from = reuse.output_asset_id, reuse.id
            stage.message = "Reused earlier result"
            await _finish_if_last(s, job, source, stage, redis)
            return
        info = await _source_info(s, source)
        inputs = await _job_inputs(s, job_id)
        project_id, stage_id = job.project_id, stage.id
        lease_id = None
        if source.original_asset_id:
            lease = AssetLease(
                asset_id=source.original_asset_id,
                job_id=job_id,
                expires_at=_now() + timedelta(seconds=settings.ffmpeg_timeout_seconds + 3600),
            )
            s.add(lease)
            await s.commit()
            lease_id = lease.id

    state: dict[str, Any] = {
        "progress": 0.0,
        "message": None,
        "last_pub": 0.0,
        "last_cancel": 0.0,
        "cancel": False,
    }
    cancel_key = CANCEL_KEY.format(job_id)
    loop_time = asyncio.get_running_loop().time

    def progress(p: float, msg: str | None) -> None:
        state["progress"] = max(0.0, min(1.0, p))
        if msg:
            state["message"] = msg
        t = loop_time()
        if t - state["last_pub"] >= 0.5:
            state["last_pub"] = t
            events.publish_sync(
                redis,
                project_id,
                {
                    "type": "job.progress",
                    "job_id": str(job_id),
                    "project_id": str(project_id),
                    "source_id": str(info.id),
                    "stage": stage_name,
                    "progress": state["progress"],
                    "message": state["message"],
                },
            )

    def cancelled() -> bool:
        t = loop_time()
        if t - state["last_cancel"] >= 1.0:
            state["last_cancel"] = t
            state["cancel"] = bool(redis.exists(cancel_key))
        return bool(state["cancel"])

    hb = asyncio.create_task(_heartbeat(sm, job_id, stage_id, state))
    try:
        with tempfile.TemporaryDirectory(dir=_scratch(settings), prefix="oc-") as tmp:
            ctx = StageContext(
                settings=settings,
                storage=storage,
                source=info,
                workdir=Path(tmp),
                progress=progress,
                cancelled=cancelled,
                job_params=job_params,
                inputs=inputs,
            )
            result: StageResult = await asyncio.to_thread(stage_def.run, ctx)
            await _store_result(sm, storage, redis, settings, job_id, stage_id, info, result)
    except StageError as exc:
        await _fail(sm, redis, job_id, stage_id, exc.code, exc.message)
    except Exception:
        log.exception("stage crashed", extra={"extra_fields": {"job_id": str(job_id), "stage": stage_name}})
        await _fail(
            sm,
            redis,
            job_id,
            stage_id,
            "internal_error",
            "Unexpected error while processing. See server logs.",
        )
    finally:
        hb.cancel()
        if lease_id:
            async with sm() as s:
                await s.execute(sa_delete(AssetLease).where(AssetLease.id == lease_id))
                await s.commit()


def _scratch(settings: Settings) -> str:
    Path(settings.scratch_dir).mkdir(parents=True, exist_ok=True)
    return settings.scratch_dir


async def _store_result(
    sm: async_sessionmaker[AsyncSession],
    storage: StorageBackend,
    redis: SyncRedis,
    settings: Settings,
    job_id: uuid.UUID,
    stage_id: uuid.UUID,
    info: SourceInfo,
    result: StageResult,
) -> None:
    created: list[tuple[uuid.UUID, str]] = []
    first_output: uuid.UUID | None = None
    try:
        async with sm() as s:
            job = await s.get(ProcessingJob, job_id)
            stage = await s.get(JobStage, stage_id)
            assert job and stage
            source = await s.get(VideoSource, info.id)
            assert source
            for out in result.outputs:
                key = new_key(info.project_id, out.kind, out.ext)
                await asyncio.to_thread(storage.put_file, out.path, key, move=True)
                st = storage.stat(key)
                asset = MediaAsset(
                    project_id=info.project_id,
                    kind=out.kind,
                    storage_backend=storage.name,
                    storage_key=key,
                    size_bytes=st.size if st else out.path.stat().st_size if out.path.exists() else 0,
                    mime=out.mime,
                    duration_s=out.duration_s,
                    width=out.width,
                    height=out.height,
                )
                s.add(asset)
                await s.flush()
                created.append((asset.id, key))
                if out.source_field:
                    setattr(source, out.source_field, asset.id)
                first_output = first_output or asset.id
            for k, v in result.source_updates.items():
                setattr(source, k, v)
            if result.transcript is not None:
                from app.services import transcripts

                await transcripts.persist_asr(s, source=source, job=job, data=result.transcript)
            if result.clip_render is not None:
                from app.services import clips

                await clips.persist_render(s, job=job, data=result.clip_render)
            if result.highlights is not None:
                from app.services import highlights

                await highlights.persist_run(s, source=source, job=job, data=result.highlights)
            stage.status, stage.progress, stage.finished_at = "succeeded", 1.0, _now()
            stage.output_asset_id, stage.message = first_output, None
            await _finish_if_last(s, job, source, stage, redis)
    except Exception:
        for _, key in created:  # don't leave orphaned objects behind if the DB write failed
            storage.delete(key)
        raise


async def _finish_if_last(
    s: AsyncSession, job: ProcessingJob, source: VideoSource, stage: JobStage, redis: SyncRedis
) -> None:
    await s.flush()
    remaining = (
        await s.execute(
            select(JobStage.id).where(JobStage.job_id == job.id, JobStage.status.notin_(("succeeded", "skipped")))
        )
    ).first()
    if remaining is None:
        job.status, job.finished_at = "succeeded", _now()
        if job.kind == ANALYZE:
            source.status = "ready"
    await s.commit()
    events.publish_sync(redis, job.project_id, _event(job, stage, source))
