from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool

from app.api.deps import DbDep, SettingsDep, StorageDep
from app.db.models import MediaAsset
from app.errors import AppError, forbidden, not_found
from app.security.media_tokens import verify_asset

router = APIRouter(prefix="/media", tags=["media"])
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
SERVABLE = {
    "proxy",
    "waveform",
    "thumbnail",
    "scenes",
    "clip_render",
    "clip_preview",
}  # originals are never served to browsers
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def download_name(raw: str) -> str:
    """A filename we choose to trust: letters, digits, dot, dash, underscore, never a path, and an .mp4 ending."""
    base = _SAFE_NAME.sub("-", raw.rsplit("/", 1)[-1]).strip(".-_")[:80] or "clip"
    return base if base.lower().endswith(".mp4") else f"{base}.mp4"


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Single byte range -> (start, end) inclusive; None = whole file. Raises 416 when unsatisfiable."""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None  # malformed or multi-range: serve the whole file (allowed by RFC 9110)
    if m.group(1) == "":  # suffix: last N bytes
        n = int(m.group(2))
        if n == 0:
            raise AppError(
                "range_not_satisfiable",
                "Range not satisfiable",
                416,
                headers={"Content-Range": f"bytes */{size}"},
            )
        return max(0, size - n), size - 1
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if start >= size or start > end:
        raise AppError(
            "range_not_satisfiable",
            "Range not satisfiable",
            416,
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, end


@router.get("/{asset_id}")
@router.head("/{asset_id}")
async def stream(
    asset_id: uuid.UUID,
    t: str,
    request: Request,
    session: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
    dl: int = 0,
    name: str = "",
) -> Response:
    verify_asset(t, asset_id, settings)
    asset = await session.get(MediaAsset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise not_found("File")
    if asset.kind not in SERVABLE:
        raise forbidden()
    st = storage.stat(asset.storage_key)
    if st is None:
        raise not_found("File")
    rng = parse_range(request.headers.get("range"), st.size)
    start, end = rng if rng else (0, st.size - 1)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, max-age=300",
    }
    if dl and asset.kind == "clip_render":  # the signed link is what authorises it; the name is only a label
        headers["Content-Disposition"] = f'attachment; filename="{download_name(name)}"'
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{st.size}"
    status = 206 if rng else 200
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=asset.mime)
    body = iterate_in_threadpool(storage.open_read(asset.storage_key, start, end))
    return StreamingResponse(body, status_code=status, headers=headers, media_type=asset.mime)
