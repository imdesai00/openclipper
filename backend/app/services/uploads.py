"""tus 1.0.0 resumable uploads (creation, termination, expiration). Bytes are staged on local disk
and moved into the storage backend on completion, so the same code serves local and S3 storage."""

from __future__ import annotations

import base64
import binascii
import logging
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.adapters.sources.direct_url import ALLOWED_EXT, safe_filename
from app.adapters.storage.keys import new_key
from app.config import Settings
from app.db.models import MediaAsset, Upload, User
from app.errors import AppError, not_found
from app.ports.storage import StorageBackend
from app.services import jobs, sources
from app.services.access import Need, require_project

log = logging.getLogger(__name__)
WRITE_BUFFER = 4 * 1024 * 1024


def parse_metadata(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not header:
        return out
    for pair in header.split(","):
        parts = pair.strip().split(" ")
        key = parts[0]
        if not key or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", key):
            raise AppError("bad_metadata", "Invalid Upload-Metadata", 400)
        try:
            out[key] = base64.b64decode(parts[1], validate=True).decode() if len(parts) > 1 else ""
        except (binascii.Error, UnicodeDecodeError, IndexError) as exc:
            raise AppError("bad_metadata", "Invalid Upload-Metadata encoding", 400) from exc
    return out


def _staging(settings: Settings, name: str) -> Path:
    root = Path(settings.upload_staging_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / name


async def create_upload(
    session: AsyncSession, settings: Settings, user: User, *, length: int, metadata: dict[str, str]
) -> Upload:
    try:
        project_id = uuid.UUID(metadata.get("project_id", ""))
    except ValueError as exc:
        raise AppError("bad_metadata", "project_id is required", 400) from exc
    await require_project(session, user, project_id, Need.WRITE)
    if metadata.get("attested") != "true":
        raise AppError(
            "attestation_required",
            "Confirm that you own this content or have permission to use it",
            400,
        )
    if length <= 0:
        raise AppError("bad_length", "Upload-Length must be positive", 400)
    if length > settings.max_upload_bytes:
        raise AppError(
            "too_large",
            f"Files larger than {settings.max_upload_bytes // 1024**2} MB are not accepted",
            413,
        )
    filename = safe_filename(metadata.get("filename", "video"))
    if Path(filename).suffix.lower() not in ALLOWED_EXT:  # first gate only; ffprobe is the real check
        raise AppError(
            "unsupported_type",
            "Unsupported file type. Upload a video (mp4, mov, mkv, webm, ...) or audio (mp3, m4a, wav, ...) file.",
            415,
        )
    name = uuid.uuid4().hex
    path = _staging(settings, name)
    path.touch(mode=0o600)
    up = Upload(
        project_id=project_id,
        user_id=user.id,
        filename=filename,
        upload_length=length,
        upload_offset=0,
        staging_name=name,
        status="active",
        attested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.upload_ttl_seconds),
    )
    session.add(up)
    await session.commit()
    return up


async def get_upload(session: AsyncSession, user: User, upload_id: uuid.UUID, *, lock: bool = False) -> Upload:
    q = select(Upload).where(Upload.id == upload_id)
    if lock:
        q = q.with_for_update(nowait=True)
    try:
        up = (await session.execute(q)).scalar_one_or_none()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":  # another PATCH holds this upload
            raise AppError("upload_locked", "Another request is writing to this upload", 423) from exc
        raise
    if up is None or up.user_id != user.id or up.expires_at <= datetime.now(UTC):
        raise not_found("Upload")  # uploads belong to the user who started them
    return up


async def append(
    session: AsyncSession,
    settings: Settings,
    storage: StorageBackend,
    user: User,
    upload_id: uuid.UUID,
    *,
    offset: int,
    body: AsyncIterator[bytes],
    ip: str | None,
) -> tuple[Upload, uuid.UUID | None]:
    up = await get_upload(session, user, upload_id, lock=True)
    if up.status != "active":
        raise AppError("upload_finished", "This upload is already complete", 409)
    if offset != up.upload_offset:
        raise AppError("offset_mismatch", "Upload-Offset does not match the server's offset", 409)
    path = _staging(settings, up.staging_name)
    written = 0
    f = await run_in_threadpool(path.open, "r+b")
    try:
        await run_in_threadpool(f.seek, offset)
        await run_in_threadpool(f.truncate)  # drop any bytes beyond the committed offset
        buf = bytearray()
        try:
            async for chunk in body:
                if offset + written + len(buf) + len(chunk) > up.upload_length:
                    raise AppError("too_much_data", "More data than Upload-Length", 413)
                if written + len(buf) + len(chunk) > settings.upload_max_chunk_bytes:
                    raise AppError("chunk_too_large", "Chunk too large; send smaller PATCH requests", 413)
                buf += chunk
                if len(buf) >= WRITE_BUFFER:
                    await run_in_threadpool(f.write, bytes(buf))
                    written += len(buf)
                    buf.clear()
        finally:
            if buf:  # keep what arrived, even if the client disconnected: it can resume from here
                await run_in_threadpool(f.write, bytes(buf))
                written += len(buf)
            await run_in_threadpool(f.flush)
    finally:
        f.close()
        up.upload_offset = offset + written
        await session.commit()

    source_id: uuid.UUID | None = None
    if up.upload_offset == up.upload_length:
        source_id = await _complete(session, settings, storage, user, up, path, ip)
    return up, source_id


async def _complete(
    session: AsyncSession,
    settings: Settings,
    storage: StorageBackend,
    user: User,
    up: Upload,
    path: Path,
    ip: str | None,
) -> uuid.UUID:
    ext = Path(up.filename).suffix.lstrip(".")
    key = new_key(up.project_id, "original", ext)
    await run_in_threadpool(storage.put_file, path, key, move=True)
    asset = MediaAsset(
        project_id=up.project_id,
        kind="original",
        storage_backend=storage.name,
        storage_key=key,
        size_bytes=up.upload_length,
        mime="application/octet-stream",
    )
    try:
        src, job = await sources.create_from_staged_upload(
            session,
            user,
            up.project_id,
            filename=up.filename,
            title=Path(up.filename).stem or up.filename,
            asset=asset,
            ip=ip,
        )
        up.status, up.source_id = "completed", src.id
        await session.commit()
    except Exception:
        await session.rollback()
        await run_in_threadpool(storage.delete, key)  # do not orphan the stored file
        raise
    jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), "upload")
    return src.id


async def terminate(session: AsyncSession, settings: Settings, user: User, upload_id: uuid.UUID) -> None:
    up = await get_upload(session, user, upload_id, lock=True)
    if up.status == "completed":
        raise AppError("upload_finished", "This upload is already complete", 409)
    _staging(settings, up.staging_name).unlink(missing_ok=True)
    await session.delete(up)
    await session.commit()


async def purge_expired(session: AsyncSession, settings: Settings) -> int:
    rows = list(
        (
            await session.execute(
                select(Upload).where(Upload.status == "active", Upload.expires_at <= datetime.now(UTC))
            )
        ).scalars()
    )
    for up in rows:
        _staging(settings, up.staging_name).unlink(missing_ok=True)
        await session.delete(up)
    await session.commit()
    return len(rows)
