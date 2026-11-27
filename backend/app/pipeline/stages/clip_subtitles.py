"""Stage: write the caption file (ASS) for a clip from the transcript words and the caption style."""

from __future__ import annotations

from app.pipeline.stages.clip_common import render_params, style_of, words_of
from app.pipeline.types import OutputFile, StageContext, StageError, StageResult
from app.services import caption_engine as ce
from app.services import render_plan as rp

params = render_params


def run(ctx: StageContext) -> StageResult:
    jp = ctx.job_params
    style = style_of(jp)
    try:
        start, end, aspect = int(jp["start_ms"]), int(jp["end_ms"]), str(jp["aspect"])
        w, h = rp.output_size(str(jp["kind"]), aspect)
    except (KeyError, ValueError) as exc:
        raise StageError("bad_job", "This render is missing its clip settings.") from exc
    cues = ce.build_cues(words_of(jp), start, end, style) if jp.get("captions_enabled", True) else []
    ctx.progress(0.5, "Writing captions")
    out = ctx.workdir / "captions.ass"
    out.write_text(ce.compile_ass(style, cues, w, h, title=str(jp.get("title", "clip"))[:80]), encoding="utf-8")
    return StageResult(outputs=[OutputFile(kind="subtitle", path=out, mime="text/x-ssa", source_field=None, ext="ass")])
