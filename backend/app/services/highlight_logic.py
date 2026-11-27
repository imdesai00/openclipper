"""Pure highlight logic: no database, no network, no clock. Everything here is exercised directly by unit tests.

The one rule that shapes the whole module (SPEC section 6.E): a model may only *point at* transcript segments by id.
Times come from the stored words, and text is always read back from the transcript. A model can therefore be wrong about
what is interesting, but it cannot invent a timestamp or a quotation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

CATEGORIES = (
    "conversation",
    "humour",
    "emotion",
    "education",
    "opinion",
    "surprising_fact",
    "controversy",
    "tutorial",
    "qa",
    "story",
    "other",
)

DEFAULTS: dict[str, Any] = {
    "min_seconds": 20,
    "max_seconds": 90,
    "max_candidates": 10,
    "pad_before_ms": 150,
    "pad_after_ms": 250,
    "snap_seconds": 10,  # how far a boundary may move to reach a sentence boundary
    "pause_ms": 700,  # a silence this long also ends a "sentence" (transcripts without punctuation)
    "window_overlap": 0.15,
    "chars_per_token": 3.0,  # deliberately pessimistic: overestimating tokens is safe, underestimating truncates
    "fallback_context_tokens": 4096,  # used when the provider does not report the model's context length
    "max_window_tokens": 3000,  # upper bound on transcript text per model call, whatever the model allows
    "prompt_reserve_tokens": 900,
    "output_reserve_tokens": 1200,
    "max_overlap": 0.3,
    "target_wps": 2.5,  # words per second that count as "dense" speech
    "scene_tolerance_ms": 500,
    # Uncalibrated heuristics, not measured against any outcome. Components that cannot be computed for a source
    # (no scene cuts, no audio) are left out and the rest renormalised.
    "weights": {
        "llm": 0.50,
        "category": 0.10,
        "standalone": 0.15,
        "density": 0.05,
        "scene": 0.05,
        "energy": 0.15,
    },
}

_TERMINAL = ".?!…。！？"
_ID = re.compile(r"s?(\d+)", re.IGNORECASE)


def merged_settings(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Defaults overlaid with whatever the admin saved. Unknown keys are ignored, bad types fall back to the default."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in (stored or {}).items():
        if k not in DEFAULTS:
            continue
        if k == "weights" and isinstance(v, dict):
            out["weights"] = {n: float(v.get(n, d)) for n, d in DEFAULTS["weights"].items()}
        elif isinstance(v, type(DEFAULTS[k])) or (isinstance(DEFAULTS[k], float) and isinstance(v, int)):
            out[k] = v
    return out


# ----------------------------------------------------------------------------------------------- transcript view
@dataclass(frozen=True)
class Word:
    pos: int  # position in the whole transcript, 0-based
    seg: int  # segment idx
    idx: int  # word idx inside the segment
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Seg:
    idx: int
    start_ms: int
    end_ms: int
    text: str
    first: int  # position of its first word (-1 if it has none)
    last: int


@dataclass
class Transcript:
    segs: list[Seg]
    words: list[Word]
    duration_ms: int
    _by_idx: dict[int, Seg] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_idx = {s.idx: s for s in self.segs}

    def seg(self, idx: int) -> Seg | None:
        return self._by_idx.get(idx)

    def text_of(self, a: int, b: int) -> str:
        return " ".join(w.text for w in self.words[a : b + 1])


def build_transcript(
    rows: list[tuple[int, int, int, str, list[tuple[int, str, int, int]]]], duration_ms: int
) -> Transcript:
    """rows: (segment idx, start_ms, end_ms, text, [(word idx, word text, start_ms, end_ms)])."""
    segs: list[Seg] = []
    words: list[Word] = []
    for idx, s, e, text, ws in sorted(rows, key=lambda r: r[0]):
        first = len(words) if ws else -1
        for widx, wtext, ws_, we_ in sorted(ws, key=lambda w: w[0]):
            words.append(Word(len(words), idx, widx, wtext.strip(), ws_, we_))
        segs.append(Seg(idx, s, e, text.strip(), first, len(words) - 1 if ws else -1))
    return Transcript(segs, words, duration_ms)


# ----------------------------------------------------------------------------------------------- windows
@dataclass(frozen=True)
class Window:
    index: int
    seg_ids: tuple[int, ...]
    lines: tuple[str, ...]
    tokens: int  # estimate

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def estimate_tokens(text: str, chars_per_token: float) -> int:
    return max(1, int(len(text) / chars_per_token) + 1)


def _stamp(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def segment_line(seg: Seg) -> str:
    return f"[s{seg.idx} {_stamp(seg.start_ms)}] {seg.text}"


def make_windows(t: Transcript, budget_tokens: int, overlap: float, chars_per_token: float) -> list[Window]:
    """Cut the transcript into overlapping runs of whole segments that each fit the token budget.

    Every segment lands in at least one window, windows overlap by about `overlap` of their segments so a highlight
    that straddles a cut is seen whole by at least one of them, and a segment larger than the budget gets a window
    of its own (it cannot be split without making up ids).
    """
    segs = [s for s in t.segs if s.text]
    if not segs:
        return []
    costs = [estimate_tokens(segment_line(s), chars_per_token) for s in segs]
    out: list[Window] = []
    start = 0
    while start < len(segs):
        end, used = start, 0
        while end < len(segs) and (end == start or used + costs[end] <= budget_tokens):
            used += costs[end]
            end += 1
        chosen = segs[start:end]
        out.append(
            Window(
                len(out),
                tuple(s.idx for s in chosen),
                tuple(segment_line(s) for s in chosen),
                used,
            )
        )
        if end >= len(segs):
            break
        keep = max(1, round(len(chosen) * overlap))
        start = max(start + 1, end - keep)  # always moves forward, so it always terminates
    return out


# ----------------------------------------------------------------------------------------------- the model's answer
def _clamp(v: Any) -> int:
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        raise ValueError("must be a number from 0 to 100") from None


def parse_segment_id(v: Any) -> int:
    """Accepts 12, "12", "s12", "[s12]". Anything else is an error the model is asked to repair."""
    if isinstance(v, bool):
        raise ValueError("must be a segment id such as s12")
    if isinstance(v, int):
        return v
    m = _ID.fullmatch(str(v).strip().strip("[]").strip())
    if not m:
        raise ValueError("must be a segment id such as s12")
    return int(m.group(1))


class RawCandidate(BaseModel):
    start_segment_id: int
    end_segment_id: int
    category: str = "other"
    hook: str = Field(default="", max_length=400)
    title: str = Field(default="", max_length=200)
    explanation: str = Field(default="", max_length=1200)
    score: int = 0
    standalone: int = 50
    confidence: int = 50

    @field_validator("start_segment_id", "end_segment_id", mode="before")
    @classmethod
    def _ids(cls, v: Any) -> int:
        return parse_segment_id(v)

    @field_validator("score", "standalone", "confidence", mode="before")
    @classmethod
    def _scores(cls, v: Any) -> int:
        return _clamp(v)

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v: Any) -> str:
        c = str(v or "other").strip().lower().replace(" ", "_").replace("-", "_")
        return c if c in CATEGORIES else "other"

    @field_validator("hook", "title", "explanation", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return str(v or "").strip()


class RawResult(BaseModel):
    candidates: list[RawCandidate] = Field(default_factory=list)


SYSTEM_PROMPT = """You find the best short clips in a transcript.

The transcript is a list of lines like "[s12 01:05] words spoken". "s12" is the segment id; the number after it is
the time. Rules:
- Pick a clip by giving the id of its FIRST line and the id of its LAST line (both inclusive). Use only ids that appear
  in the transcript below. Never write times and never quote text.
- A clip must make sense on its own: it starts at the beginning of a thought and ends when the thought is finished.
- Prefer clips between {min_s} and {max_s} seconds long.
- score: how worthwhile the clip is for your reader, 0 to 100. standalone: how well it makes sense without the rest of
  the video, 0 to 100. confidence: how sure you are, 0 to 100.
- category: one of {categories}.
- hook: the opening line of a caption that makes someone want to watch. title: a short title. explanation: one or two
  sentences on why this clip works.
- If nothing in this part of the transcript is worth clipping, return an empty list. Do not invent clips.
Answer with JSON only, in this shape:
{{"candidates": [{{"start_segment_id": "s12", "end_segment_id": "s15", "category": "...",
"hook": "...", "title": "...", "explanation": "...", "score": 0, "standalone": 0, "confidence": 0}}]}}"""


def build_messages(
    window: Window, *, prompt: str, categories: list[str], min_s: int, max_s: int, max_per_window: int
) -> list[tuple[str, str]]:
    system = SYSTEM_PROMPT.format(min_s=min_s, max_s=max_s, categories=", ".join(CATEGORIES))
    ask = []
    if prompt.strip():
        ask.append(f"What the user is looking for: {prompt.strip()}")
    if categories:
        ask.append("Only categories that matter: " + ", ".join(categories))
    ask.append(f"Return at most {max_per_window} clips from this part.")
    user = "\n".join(ask) + "\n\nTranscript:\n" + window.text
    return [("system", system), ("user", user)]


# ----------------------------------------------------------------------------------------------- snapping
@dataclass(frozen=True)
class Limits:
    min_ms: int
    max_ms: int
    snap_ms: int
    pause_ms: int
    pad_before_ms: int
    pad_after_ms: int


def is_sentence_end(t: Transcript, pos: int, pause_ms: int) -> bool:
    w = t.words[pos]
    if pos == len(t.words) - 1:
        return True
    text = w.text.rstrip("\"'”’)]}")
    if text and text[-1] in _TERMINAL:
        return True
    return t.words[pos + 1].start_ms - w.end_ms >= pause_ms


def is_sentence_start(t: Transcript, pos: int, pause_ms: int) -> bool:
    return pos == 0 or is_sentence_end(t, pos - 1, pause_ms)


def _nearest(
    t: Transcript, pos: int, step: int, limit_ms: int, ok: Callable[[int], bool], anchor_ms: int
) -> int | None:
    p = pos
    while 0 <= p < len(t.words):
        if abs(t.words[p].start_ms - anchor_ms) > limit_ms and abs(t.words[p].end_ms - anchor_ms) > limit_ms:
            return None
        if ok(p):
            return p
        p += step
    return None


def snap_start(t: Transcript, pos: int, lim: Limits) -> tuple[int, bool]:
    """Move a start to the nearest sentence start, looking back and forward. The bool says whether it landed."""

    def ok(p: int) -> bool:
        return is_sentence_start(t, p, lim.pause_ms)

    if ok(pos):
        return pos, True
    anchor = t.words[pos].start_ms
    back = _nearest(t, pos, -1, lim.snap_ms, ok, anchor)
    fwd = _nearest(t, pos, 1, lim.snap_ms, ok, anchor)
    if back is None and fwd is None:
        return pos, False
    if back is None:
        return fwd if fwd is not None else pos, True
    if fwd is None:
        return back, True
    return (back, True) if anchor - t.words[back].start_ms <= t.words[fwd].start_ms - anchor else (fwd, True)


def snap_end(t: Transcript, pos: int, lim: Limits) -> tuple[int, bool]:
    def ok(p: int) -> bool:
        return is_sentence_end(t, p, lim.pause_ms)

    if ok(pos):
        return pos, True
    anchor = t.words[pos].end_ms
    fwd = _nearest(t, pos, 1, lim.snap_ms, ok, anchor)
    back = _nearest(t, pos, -1, lim.snap_ms, ok, anchor)
    if fwd is None and back is None:
        return pos, False
    if fwd is None:
        return back if back is not None else pos, True
    if back is None:
        return fwd, True
    return (fwd, True) if t.words[fwd].end_ms - anchor <= anchor - t.words[back].end_ms else (back, True)


@dataclass(frozen=True)
class Span:
    start_pos: int
    end_pos: int
    snapped: bool  # both ends landed on sentence boundaries


def _duration(t: Transcript, a: int, b: int) -> int:
    return t.words[b].end_ms - t.words[a].start_ms


def fit_duration(t: Transcript, a: int, b: int, lim: Limits) -> tuple[int, int] | str:
    """Make [a, b] obey min/max by moving the END along sentence ends, then the start. A string means: drop it."""
    d = _duration(t, a, b)
    if lim.min_ms <= d <= lim.max_ms:
        return a, b
    if d > lim.max_ms:
        p = b
        while p >= a:
            if is_sentence_end(t, p, lim.pause_ms) and lim.min_ms <= _duration(t, a, p) <= lim.max_ms:
                return a, p
            p -= 1
        return "too_long"
    p = b
    while p + 1 < len(t.words):  # too short: take in following sentences
        p += 1
        if _duration(t, a, p) > lim.max_ms:
            break
        if is_sentence_end(t, p, lim.pause_ms) and _duration(t, a, p) >= lim.min_ms:
            return a, p
    q = a
    while q - 1 >= 0:  # or the sentences before it
        q -= 1
        if _duration(t, q, b) > lim.max_ms:
            break
        if is_sentence_start(t, q, lim.pause_ms) and _duration(t, q, b) >= lim.min_ms:
            return q, b
    return "too_short"


def padded_range(t: Transcript, a: int, b: int, lim: Limits) -> tuple[int, int]:
    """Playback range: the words plus padding, never into the neighbouring words and never outside the media."""
    wa, wb = t.words[a], t.words[b]
    gap_before = wa.start_ms - (t.words[a - 1].end_ms if a > 0 else 0)
    gap_after = (t.words[b + 1].start_ms if b + 1 < len(t.words) else t.duration_ms) - wb.end_ms
    start = wa.start_ms - min(lim.pad_before_ms, max(0, gap_before) // 2)
    end = wb.end_ms + min(lim.pad_after_ms, max(0, gap_after) // 2)
    return max(0, start), min(max(t.duration_ms, wb.end_ms), end)


@dataclass
class Resolved:
    raw: RawCandidate
    start_pos: int
    end_pos: int
    start_ms: int
    end_ms: int
    snapped: bool
    window: int


def resolve(t: Transcript, raw: RawCandidate, window: Window, lim: Limits) -> Resolved | str:
    """Turn the model's two ids into real words, or say why not. The returned string is a drop reason."""
    ids = set(window.seg_ids)
    if raw.start_segment_id not in ids or raw.end_segment_id not in ids:
        return "unknown_segment"  # the model named a segment it was not shown: refuse, never guess
    if raw.start_segment_id > raw.end_segment_id:
        return "reversed"
    s, e = t.seg(raw.start_segment_id), t.seg(raw.end_segment_id)
    if s is None or e is None or s.first < 0 or e.last < 0:
        return "no_words"
    a, a_ok = snap_start(t, s.first, lim)
    b, b_ok = snap_end(t, e.last, lim)
    if b < a:
        return "reversed"
    fitted = fit_duration(t, a, b, lim)
    if isinstance(fitted, str):
        return fitted
    a2, b2 = fitted
    ok = (a2 == a or is_sentence_start(t, a2, lim.pause_ms)) and (b2 == b or is_sentence_end(t, b2, lim.pause_ms))
    start_ms, end_ms = padded_range(t, a2, b2, lim)
    return Resolved(raw, a2, b2, start_ms, end_ms, bool(a_ok and b_ok and ok), window.index)


# ----------------------------------------------------------------------------------------------- scoring
def speech_density_score(t: Transcript, a: int, b: int, target_wps: float) -> float:
    seconds = max(0.001, _duration(t, a, b) / 1000)
    return max(0.0, min(100.0, 100.0 * ((b - a + 1) / seconds) / target_wps))


def scene_alignment_score(start_ms: int, end_ms: int, cuts_ms: list[int], tol_ms: int) -> float | None:
    """100 when both ends sit on a scene cut, 50 for one end, 0 for none. None when the video has no cuts at all."""
    if not cuts_ms:
        return None
    hit = sum(1 for edge in (start_ms, end_ms) if any(abs(edge - c) <= tol_ms for c in cuts_ms))
    return 50.0 * hit


def energy_reference(peaks: list[list[int]]) -> float | None:
    """The 90th percentile of loudness in the whole source, used to judge a span against its own recording."""
    levels = sorted(hi - lo for lo, hi in peaks)
    if not levels or levels[-1] <= 0:
        return None
    return float(levels[min(len(levels) - 1, int(0.9 * len(levels)))]) or None


def audio_energy_score(
    start_ms: int, end_ms: int, duration_ms: int, peaks: list[list[int]], ref: float | None
) -> float | None:
    if not peaks or not ref or duration_ms <= 0:
        return None
    n = len(peaks)
    i, j = int(start_ms / duration_ms * n), max(int(end_ms / duration_ms * n), int(start_ms / duration_ms * n) + 1)
    chunk = peaks[max(0, i) : min(n, j)]
    if not chunk:
        return None
    mean = sum(hi - lo for lo, hi in chunk) / len(chunk)
    return max(0.0, min(100.0, 100.0 * mean / ref))


def combine(parts: dict[str, float | None], weights: dict[str, float]) -> tuple[float, dict[str, Any]]:
    """Weighted mean over the components that exist. Returns the score and an explanation of every component."""
    avail = {k: v for k, v in parts.items() if v is not None and weights.get(k, 0) > 0}
    total = sum(weights[k] for k in avail)
    if not avail or total <= 0:
        return 0.0, {"unavailable": sorted(parts)}
    score = sum(weights[k] * v for k, v in avail.items()) / total
    detail: dict[str, Any] = {
        k: {"value": round(v, 1), "weight": round(weights[k] / total, 3)} for k, v in avail.items()
    }
    detail["unavailable"] = sorted(k for k, v in parts.items() if v is None or weights.get(k, 0) <= 0)
    return round(score, 2), detail


def overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    if inter <= 0:
        return 0.0
    return inter / max(1, min(a[1] - a[0], b[1] - b[0]))


def dedupe(items: list[Any], max_overlap: float) -> tuple[list[Any], int]:
    """Keep the strongest first; drop later items that overlap a kept one too much (need start_ms, end_ms, final)."""
    kept: list[Any] = []
    dropped = 0
    for it in sorted(items, key=lambda x: (-x.final, x.start_ms)):
        if any(overlap_ratio((it.start_ms, it.end_ms), (k.start_ms, k.end_ms)) > max_overlap for k in kept):
            dropped += 1
            continue
        kept.append(it)
    return kept, dropped


# ----------------------------------------------------------------------------------------------- what a run produces
@dataclass
class Scored:
    """A resolved candidate with its score. `final` is what ranks it; the parts say how it was made."""

    resolved: Resolved
    llm: int
    standalone: int
    confidence: int
    final: float
    parts: dict[str, Any]
    provider_name: str
    model_name: str

    @property
    def start_ms(self) -> int:
        return self.resolved.start_ms

    @property
    def end_ms(self) -> int:
        return self.resolved.end_ms


@dataclass
class RunData:
    """Everything a finished highlight job hands to the runner to store."""

    transcript_id: str
    params: dict[str, Any]
    params_hash: str
    candidates: list[Scored]
    stats: dict[str, Any]
    transcript: Transcript | None = None
