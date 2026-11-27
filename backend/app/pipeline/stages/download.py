"""Stage: fetch the original for link-based sources (direct URL or yt-dlp)."""

from __future__ import annotations

from app.adapters.sources.direct_url import DownloadError, download
from app.config import Settings
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult


def params(s: Settings, job_params: dict[str, object] | None = None) -> dict[str, object]:
    return {"v": 1, "max_height": s.ytdlp_max_height, "max_bytes": s.direct_url_max_bytes}


def run(ctx: StageContext) -> StageResult:
    src, s = ctx.source, ctx.settings
    if not src.origin_url:
        raise StageError("no_url", "This source has no link")
    if src.kind == "direct_url":
        try:
            got = download(
                src.origin_url,
                ctx.workdir,
                max_bytes=s.direct_url_max_bytes,
                timeout_s=s.direct_url_timeout_seconds,
                allow_private=s.direct_url_allow_private,
                progress=ctx.progress,
                cancelled=ctx.cancelled,
            )
        except DownloadError as exc:
            raise StageError(exc.code, exc.message) from exc
        path, filename, size, ext = got.path, got.filename, got.size, got.ext
        title = None
    elif src.kind == "ytdlp":
        from app.adapters.sources.ytdlp import (
            YtDlpError,
            download_video,
        )  # only present in the ingest image

        try:
            res = download_video(
                src.origin_url,
                ctx.workdir,
                settings=s,
                progress=ctx.progress,
                cancelled=ctx.cancelled,
            )
        except YtDlpError as exc:
            raise StageError(exc.code, exc.message) from exc
        path, filename, size, ext, title = res.path, res.filename, res.size, res.ext, res.title
    else:
        raise StageError("bad_kind", f"Source kind {src.kind} is not downloadable")
    updates: dict[str, object] = {"original_filename": filename, "size_bytes": size}
    if title:
        updates["title"] = title[:500]
    return StageResult(
        outputs=[
            OutputFile(
                kind="original", path=path, mime="application/octet-stream", source_field="original_asset_id", ext=ext
            )
        ],
        source_updates=updates,
    )
