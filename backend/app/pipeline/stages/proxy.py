"""Stage: low-resolution H.264/AAC proxy for browser editing (faststart, seek-friendly GOP)."""

from __future__ import annotations

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg
from app.adapters.media.ffprobe import MediaValidationError, probe
from app.config import Settings
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult


def params(s: Settings) -> dict[str, object]:
    return {"v": 1, "height": s.proxy_height, "crf": s.proxy_crf, "preset": s.proxy_preset}


def run(ctx: StageContext) -> StageResult:
    src, s = ctx.source, ctx.settings
    if not src.original_key or not src.duration_s:
        raise StageError("no_original", "The original file is missing")
    out = ctx.workdir / "proxy.mp4"
    # Never upscale: cap the height, keep the width even, square pixels.
    vf = f"scale=-2:'min({s.proxy_height},ih)',setsar=1,format=yuv420p"
    args = [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        s.proxy_preset,
        "-crf",
        str(s.proxy_crf),
        "-g",
        "48",
        "-keyint_min",
        "48",
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
    ]
    with ctx.storage.local_path(src.original_key) as path:
        try:
            run_ffmpeg(
                input_path=path,
                output_args=args,
                output_path=out,
                duration_s=src.duration_s,
                timeout_s=s.ffmpeg_timeout_seconds,
                threads=s.ffmpeg_threads,
                on_progress=lambda p: ctx.progress(p, "Encoding proxy"),
                cancelled=ctx.cancelled,
            )
        except Cancelled as exc:
            raise StageError("cancelled", "Cancelled") from exc
        except FfmpegError as exc:
            raise StageError(exc.code, f"{exc.message}: {exc.stderr_tail[-300:]}".strip(": ")) from exc
    try:
        info = probe(out)  # verify our own output is valid before publishing it
    except MediaValidationError as exc:
        raise StageError("proxy_invalid", f"Generated proxy is not valid: {exc.message}") from exc
    return StageResult(
        outputs=[
            OutputFile(
                kind="proxy",
                path=out,
                mime="video/mp4",
                source_field="proxy_asset_id",
                ext="mp4",
                width=info.width,
                height=info.height,
                duration_s=info.duration_s,
            )
        ]
    )
