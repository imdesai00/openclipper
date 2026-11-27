"""Stage: validate the stored original with ffprobe and fingerprint it."""

from __future__ import annotations

import hashlib

from app.adapters.media.ffprobe import MediaValidationError, probe
from app.pipeline.types import StageContext, StageError, StageResult


def params(_settings: object, job_params: dict[str, object] | None = None) -> dict[str, object]:
    return {"v": 1}


def run(ctx: StageContext) -> StageResult:
    if not ctx.source.original_key:
        raise StageError("no_original", "The original file is missing")
    with ctx.storage.local_path(ctx.source.original_key) as path:
        ctx.progress(0.05, "Checking file")
        try:
            result = probe(path)
        except MediaValidationError as exc:
            raise StageError(exc.code, exc.message) from exc
        ctx.progress(0.3, "Fingerprinting")
        h = hashlib.sha256()
        total = path.stat().st_size or 1
        done = 0
        with path.open("rb") as f:
            while chunk := f.read(8 * 1024 * 1024):
                h.update(chunk)
                done += len(chunk)
                if ctx.cancelled():
                    raise StageError("cancelled", "Cancelled")
                ctx.progress(0.3 + 0.7 * done / total, None)
    return StageResult(
        source_updates={
            "media_hash": h.hexdigest(),
            "size_bytes": result.size_bytes,
            "duration_s": result.duration_s,
            "width": result.width or None,  # audio-only files have no picture
            "height": result.height or None,
            "has_video": result.has_video,
            "container": result.container[:100],
            "video_codec": result.video_codec or None,
            "audio_codec": result.audio_codec,
            "has_audio": result.has_audio,
            "probe": result.summary,
        }
    )
