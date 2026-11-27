"""Stage: waveform peaks as JSON for the editor timeline."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from app.adapters.media.ffmpeg import Cancelled, FfmpegError, run_ffmpeg_to_pipe
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult

SAMPLE_RATE = 8000
BUCKETS = 2000


def params(_settings: object) -> dict[str, object]:
    return {"v": 1, "sr": SAMPLE_RATE, "buckets": BUCKETS}


def peaks_from_samples(samples: np.ndarray, buckets: int) -> list[list[int]]:
    """Min/max per bucket scaled to int8-ish range [-127, 127] (compact JSON)."""
    if samples.size == 0:
        return []
    n = min(buckets, samples.size)
    edges = np.linspace(0, samples.size, n + 1, dtype=np.int64)
    out: list[list[int]] = []
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        seg = samples[a:b]
        out.append([int(seg.min()) * 127 // 32768, int(seg.max()) * 127 // 32768])
    return out


def rebucket(mins: list[int], maxs: list[int], target: int) -> tuple[list[int], list[int]]:
    """Merge neighbouring buckets down to `target` (decoded audio can be slightly longer than the container says)."""
    if len(mins) <= target:
        return mins, maxs
    edges = np.linspace(0, len(mins), target + 1, dtype=np.int64)
    lo = np.asarray(mins)
    hi = np.asarray(maxs)
    return (
        [int(lo[a:b].min()) for a, b in zip(edges[:-1], edges[1:], strict=True)],
        [int(hi[a:b].max()) for a, b in zip(edges[:-1], edges[1:], strict=True)],
    )


def run(ctx: StageContext) -> StageResult:
    src = ctx.source
    if not src.original_key or not src.duration_s:
        raise StageError("no_original", "The original file is missing")
    doc: dict[str, Any] = {
        "version": 1,
        "sample_rate": SAMPLE_RATE,
        "duration_s": src.duration_s,
        "has_audio": bool(src.has_audio),
        "peaks": [],
    }
    if src.has_audio:
        # Stream the decoded audio and keep only per-window min/max, so memory stays flat for long files.
        window = max(1, math.ceil(SAMPLE_RATE * src.duration_s / BUCKETS))
        mins: list[int] = []
        maxs: list[int] = []
        carry = np.empty(0, dtype=np.int16)

        def on_chunk(raw: bytes) -> None:
            nonlocal carry
            arr = np.concatenate([carry, np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16)])
            full = arr.size // window * window
            if full:
                blocks = arr[:full].reshape(-1, window)
                mins.extend(int(v) for v in blocks.min(axis=1))
                maxs.extend(int(v) for v in blocks.max(axis=1))
            carry = arr[full:]

        with ctx.storage.local_path(src.original_key) as path:
            try:
                run_ffmpeg_to_pipe(
                    input_path=path,
                    output_args=[
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        str(SAMPLE_RATE),
                        "-f",
                        "s16le",
                    ],
                    chunk_bytes=1 << 20,
                    timeout_s=ctx.settings.ffmpeg_timeout_seconds,
                    threads=ctx.settings.ffmpeg_threads,
                    on_chunk=on_chunk,
                    cancelled=ctx.cancelled,
                )
            except Cancelled as exc:
                raise StageError("cancelled", "Cancelled") from exc
            except FfmpegError as exc:
                raise StageError(exc.code, exc.message) from exc
        if carry.size:
            mins.append(int(carry.min()))
            maxs.append(int(carry.max()))
        mins, maxs = rebucket(mins, maxs, BUCKETS)
        doc["peaks"] = [[lo * 127 // 32768, hi * 127 // 32768] for lo, hi in zip(mins, maxs, strict=True)]
    ctx.progress(0.95, "Writing waveform")
    out = ctx.workdir / "waveform.json"
    out.write_text(json.dumps(doc, separators=(",", ":")))
    return StageResult(
        outputs=[
            OutputFile(kind="waveform", path=out, mime="application/json", source_field="waveform_asset_id", ext="json")
        ]
    )
