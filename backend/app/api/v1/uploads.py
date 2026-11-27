"""tus 1.0.0 server (https://tus.io/protocols/resumable-upload). Extensions: creation, termination, expiration."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response

from app.api.deps import CurrentUser, DbDep, SettingsDep, StorageDep, client_ip
from app.errors import AppError
from app.services import uploads as svc

router = APIRouter(prefix="/uploads", tags=["uploads"])
TUS = "1.0.0"


def _hdr(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"Tus-Resumable": TUS, "Cache-Control": "no-store", **(extra or {})}


def _require_tus(request: Request) -> None:
    if request.headers.get("tus-resumable") != TUS:
        raise AppError(
            "tus_version",
            "Unsupported or missing Tus-Resumable header",
            412,
            headers={"Tus-Version": TUS},
        )


def _int(request: Request, name: str) -> int:
    raw = request.headers.get(name, "")
    if not raw.isdigit():
        raise AppError("bad_header", f"{name} must be a non-negative integer", 400)
    return int(raw)


@router.options("")
async def options(settings: SettingsDep) -> Response:
    return Response(
        status_code=204,
        headers={
            "Tus-Resumable": TUS,
            "Tus-Version": TUS,
            "Tus-Extension": "creation,termination,expiration",
            "Tus-Max-Size": str(settings.max_upload_bytes),
        },
    )


@router.post("", status_code=201)
async def create(request: Request, user: CurrentUser, session: DbDep, settings: SettingsDep) -> Response:
    _require_tus(request)
    if "upload-defer-length" in request.headers:
        raise AppError("unsupported", "Upload-Defer-Length is not supported", 400)
    up = await svc.create_upload(
        session,
        settings,
        user,
        length=_int(request, "upload-length"),
        metadata=svc.parse_metadata(request.headers.get("upload-metadata")),
    )
    return Response(
        status_code=201,
        headers=_hdr(
            {
                "Location": f"/api/v1/uploads/{up.id}",
                "Upload-Offset": "0",
                "Upload-Expires": up.expires_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            }
        ),
    )


@router.head("/{upload_id}")
async def head(upload_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep) -> Response:
    _require_tus(request)
    up = await svc.get_upload(session, user, upload_id)
    h = {"Upload-Offset": str(up.upload_offset), "Upload-Length": str(up.upload_length)}
    if up.source_id:
        h["X-Source-Id"] = str(up.source_id)
    return Response(status_code=200, headers=_hdr(h))


@router.patch("/{upload_id}")
async def patch(
    upload_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> Response:
    _require_tus(request)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/offset+octet-stream":
        raise AppError("bad_content_type", "Content-Type must be application/offset+octet-stream", 415)
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.upload_max_chunk_bytes:
        raise AppError("chunk_too_large", "Chunk too large; send smaller PATCH requests", 413)
    up, source_id = await svc.append(
        session,
        settings,
        storage,
        user,
        upload_id,
        offset=_int(request, "upload-offset"),
        body=request.stream(),
        ip=client_ip(request),
    )
    h = {"Upload-Offset": str(up.upload_offset)}
    if source_id:
        h["X-Source-Id"] = str(source_id)
    return Response(status_code=204, headers=_hdr(h))


@router.delete("/{upload_id}", status_code=204)
async def terminate(
    upload_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep, settings: SettingsDep
) -> Response:
    _require_tus(request)
    await svc.terminate(session, settings, user, upload_id)
    return Response(status_code=204, headers=_hdr())
