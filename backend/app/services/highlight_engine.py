"""Run highlight detection over one transcript: window it, ask the model per window, resolve, score, rank.

Layered on purpose: highlight_logic.py holds every rule and is pure; this module only does the I/O around it (model
calls, progress, cancellation) and keeps the books on what happened, so a run can always say what it dropped and why.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from app.errors import AppError
from app.pipeline.types import StageError
from app.ports.llm import ChatMessage
from app.services import highlight_logic as hl
from app.services.llm import LLMService

TASK = "highlight"


def num_ctx_for(window_tokens: int, cfg: dict[str, Any], model_ctx: int) -> int:
    """The context size to ask the server for: the window plus room for instructions and the answer, in 512 steps."""
    need = window_tokens + int(cfg["prompt_reserve_tokens"]) + int(cfg["output_reserve_tokens"])
    return min(model_ctx, ((need + 511) // 512) * 512)


def window_budget(cfg: dict[str, Any], contexts: list[int | None]) -> tuple[int, int, str]:
    """(transcript tokens per window, model context used, where that came from).

    Sized to the SMALLEST model that could be asked (a fallback model may take over mid-run) and never above
    `max_window_tokens`, so a huge hosted context does not turn into a huge, unfocused prompt."""
    known = [c for c in contexts if c]
    ctx = min(known) if known else int(cfg["fallback_context_tokens"])
    if len(known) < len(contexts):  # some model did not report its context: be careful, use the fallback for it too
        ctx = min(ctx, int(cfg["fallback_context_tokens"]))
    source = "reported" if known and len(known) == len(contexts) else "fallback"
    room = ctx - int(cfg["prompt_reserve_tokens"]) - int(cfg["output_reserve_tokens"])
    return max(200, min(int(cfg["max_window_tokens"]), room)), ctx, source


async def detect(
    *,
    llm: LLMService,
    t: hl.Transcript,
    request: dict[str, Any],
    cfg: dict[str, Any],
    cuts_ms: list[int],
    peaks: list[list[int]],
    user_id: uuid.UUID | None,
    progress: Callable[[float, str | None], None],
    cancelled: Callable[[], bool],
) -> tuple[list[hl.Scored], dict[str, Any]]:
    model_id = uuid.UUID(request["model_id"]) if request.get("model_id") else None
    lim = hl.Limits(
        min_ms=int(request["min_seconds"] * 1000),
        max_ms=int(request["max_seconds"] * 1000),
        snap_ms=int(cfg["snap_seconds"] * 1000),
        pause_ms=int(cfg["pause_ms"]),
        pad_before_ms=int(cfg["pad_before_ms"]),
        pad_after_ms=int(cfg["pad_after_ms"]),
    )
    try:
        contexts = await llm.usable_contexts(TASK, model_id)
    except AppError as exc:  # no model set for this task
        raise StageError(exc.code, exc.message) from exc
    if not contexts:
        raise StageError("route_unusable", "None of the models for highlight detection can be used right now.")
    window_tokens, model_ctx, ctx_source = window_budget(cfg, contexts)
    windows = hl.make_windows(t, window_tokens, float(cfg["window_overlap"]), float(cfg["chars_per_token"]))
    if not windows:
        raise StageError("empty_transcript", "The transcript has no text to look at.")
    ctx_size = num_ctx_for(window_tokens, cfg, model_ctx)
    per_window = max(3, math.ceil(request["max_candidates"] / len(windows)) + 2)
    energy_ref = hl.energy_reference(peaks)
    weights = cfg["weights"]

    scored: list[hl.Scored] = []
    dropped: Counter[str] = Counter()
    failed: list[dict[str, Any]] = []
    raw_total = repairs = tokens_in = tokens_out = unsnapped = 0
    cost: Decimal | None = Decimal(0)
    models: dict[str, int] = {}

    for w in windows:
        if cancelled():
            raise StageError("cancelled", "Cancelled")
        progress(w.index / len(windows), f"Reading part {w.index + 1} of {len(windows)}")
        msgs = hl.build_messages(
            w,
            prompt=request["prompt"],
            categories=request["categories"],
            min_s=request["min_seconds"],
            max_s=request["max_seconds"],
            max_per_window=per_window,
        )
        try:
            c = await llm.complete(
                task=TASK,
                messages=[ChatMessage(r, text) for r, text in msgs],
                user_id=user_id,
                schema_model=hl.RawResult,
                temperature=0.2,
                max_tokens=int(cfg["output_reserve_tokens"]),
                model_id=model_id,
                num_ctx=ctx_size,
            )
        except AppError as exc:
            if exc.code in ("no_model_configured", "route_unusable"):
                raise StageError(exc.code, exc.message) from exc
            failed.append({"window": w.index, "code": exc.code, "message": exc.message})
            continue
        repairs += c.repairs
        tokens_in, tokens_out = tokens_in + c.tokens_in, tokens_out + c.tokens_out
        cost = None if (cost is None or c.cost_est is None) else cost + c.cost_est
        key = f"{c.provider_name} / {c.model_name}"
        models[key] = models.get(key, 0) + 1
        result = c.parsed if isinstance(c.parsed, hl.RawResult) else hl.RawResult()
        for raw in result.candidates:
            raw_total += 1
            r = hl.resolve(t, raw, w, lim)
            if isinstance(r, str):
                dropped[r] += 1
                continue
            if not r.snapped:
                unsnapped += 1
            parts = {
                "llm": float(raw.score),
                "category": (100.0 if raw.category in request["categories"] else 0.0)
                if request["categories"]
                else None,
                "standalone": float(raw.standalone),
                "density": hl.speech_density_score(t, r.start_pos, r.end_pos, float(cfg["target_wps"])),
                "scene": hl.scene_alignment_score(r.start_ms, r.end_ms, cuts_ms, int(cfg["scene_tolerance_ms"])),
                "energy": hl.audio_energy_score(r.start_ms, r.end_ms, t.duration_ms, peaks, energy_ref),
            }
            final, detail = hl.combine(parts, weights)
            scored.append(
                hl.Scored(r, raw.score, raw.standalone, raw.confidence, final, detail, c.provider_name, c.model_name)
            )

    progress(1.0, "Ranking")
    if failed and len(failed) == len(windows):
        first = failed[0]
        raise StageError(str(first["code"]), f"No part of the transcript could be analysed. {first['message']}")
    kept, overlapped = hl.dedupe(scored, float(cfg["max_overlap"]))
    dropped["overlaps_stronger"] += overlapped
    kept = kept[: int(request["max_candidates"])]
    stats: dict[str, Any] = {
        "windows": len(windows),
        "windows_failed": failed,  # shown to the user: "N parts could not be analysed"
        "window_tokens": window_tokens,
        "model_context": model_ctx,
        "context_source": ctx_source,
        "raw_candidates": raw_total,
        "dropped": dict(dropped),
        "not_on_sentence_boundary": unsnapped,
        "kept": len(kept),
        "repairs": repairs,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_est": None if cost is None else float(cost),  # None = at least one call had no known price
        "models": models,
        "scene_cuts": len(cuts_ms),
        "audio_energy": bool(peaks) and energy_ref is not None,
    }
    return kept, stats
