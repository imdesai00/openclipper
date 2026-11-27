"""Pure transcript logic: no database, no I/O. Everything here is exercised directly by unit tests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

MIN_WORD_MS = 20
_PUNCT = re.compile(r"[^\wÀ-￿']+", re.UNICODE)


@dataclass(frozen=True)
class W:
    """A word with its time span in milliseconds."""

    text: str
    start_ms: int
    end_ms: int
    probability: float | None = None


def compute_cache_key(
    *, media_hash: str, engine: str, model: str, language: str | None, vad: bool, loudnorm: bool, denoise: bool
) -> str:
    """Equal keys mean equal recognition results, so a result can be reused instead of recomputed (SPEC §6.D)."""
    blob = json.dumps(
        {
            "media": media_hash,
            "engine": engine,
            "model": model,
            "language": language or "auto",
            "vad": vad,
            "loudnorm": loudnorm,
            "denoise": denoise,
            "v": 1,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def norm(token: str) -> str:
    return _PUNCT.sub("", token).lower()


def seconds_to_ms(x: float) -> int:
    return max(0, round(x * 1000))


def tidy(words: list[W], lo: int, hi: int) -> list[W]:
    """Make a word list valid: inside [lo, hi], in order, no overlaps, each word MIN_WORD_MS long where room allows."""
    out: list[W] = []
    cursor = lo
    for i, w in enumerate(words):
        start = max(cursor, min(w.start_ms, hi))
        end = max(start, min(w.end_ms, hi))
        nxt = words[i + 1].start_ms if i + 1 < len(words) else hi
        if end - start < MIN_WORD_MS:
            room = nxt if nxt > start else hi  # a next word that starts earlier is out of order: it will be pushed back
            end = max(end, min(start + MIN_WORD_MS, room, hi))
        out.append(W(w.text, start, end, w.probability))
        cursor = end
    return out


def align_edit(old: list[W], new_text: str, seg_start_ms: int, seg_end_ms: int) -> list[W]:
    """Give the words of an edited segment timings WITHOUT re-running recognition.

    Words that survive the edit (ignoring case and punctuation) keep their exact original timing. Words that were
    replaced share the time span of the words they replaced, in proportion to their length. Inserted words use the
    gap where they were inserted. Deleted words simply disappear.
    """
    tokens = new_text.split()
    if not tokens:
        raise ValueError("A segment cannot be empty.")
    sm = SequenceMatcher(a=[norm(w.text) for w in old], b=[norm(t) for t in tokens], autojunk=False)
    out: list[W] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                o = old[i1 + k]
                out.append(W(tokens[j1 + k], o.start_ms, o.end_ms, o.probability))
            continue
        if j2 == j1:  # pure deletion
            continue
        if i2 > i1:  # replacement: use the span of the words being replaced
            span_start, span_end = old[i1].start_ms, old[i2 - 1].end_ms
        else:  # insertion: use the gap between the neighbours
            span_start = out[-1].end_ms if out else seg_start_ms
            span_end = old[i1].start_ms if i1 < len(old) else seg_end_ms
        span_end = max(span_end, span_start)
        chunk = tokens[j1:j2]
        weights = [len(t) + 1 for t in chunk]
        total = sum(weights)
        t = span_start
        for tok, wt in zip(chunk, weights, strict=True):
            e = t + round((span_end - span_start) * wt / total)
            out.append(W(tok, t, e, None))
            t = e
        out[-1] = W(out[-1].text, out[-1].start_ms, max(out[-1].end_ms, span_end), None)
    return tidy(out, seg_start_ms, seg_end_ms)


# ----------------------------------------------------------------------------------------------- subtitles
MAX_CUE_CHARS = 84  # two lines of 42
MAX_LINE_CHARS = 42
MAX_CUE_MS = 6000
MAX_GAP_MS = 1200


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str  # may contain one newline


def _two_lines(text: str) -> str:
    if len(text) <= MAX_LINE_CHARS or " " not in text:
        return text
    mid = len(text) / 2
    best = min((i for i, c in enumerate(text) if c == " "), key=lambda i: abs(i - mid))
    return text[:best] + "\n" + text[best + 1 :]


def _cue(words: list[W]) -> Cue:
    return Cue(
        words[0].start_ms,
        max(words[-1].end_ms, words[0].start_ms + MIN_WORD_MS),
        _two_lines(" ".join(w.text for w in words)),
    )


def _must_break(cur: list[W], w: W) -> bool:
    text_len = len(" ".join(x.text for x in cur)) + 1 + len(w.text)
    return (
        text_len > MAX_CUE_CHARS or w.end_ms - cur[0].start_ms > MAX_CUE_MS or w.start_ms - cur[-1].end_ms > MAX_GAP_MS
    )


def make_cues(segments: list[list[W]]) -> list[Cue]:
    """Cut words into readable cues: never across a segment, over 84 characters or 6 s, or across a long pause."""
    cues: list[Cue] = []
    for words in segments:
        cur: list[W] = []
        for w in words:
            if cur and _must_break(cur, w):
                cues.append(_cue(cur))
                cur = []
            cur.append(w)
        if cur:
            cues.append(_cue(cur))
    for i in range(len(cues) - 1):  # cues must not overlap
        if cues[i].end_ms > cues[i + 1].start_ms:
            cues[i] = Cue(cues[i].start_ms, max(cues[i].start_ms + 1, cues[i + 1].start_ms), cues[i].text)
    return cues


def _ts(ms: int, sep: str) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "\n".join(f"{i}\n{_ts(c.start_ms, ',')} --> {_ts(c.end_ms, ',')}\n{c.text}\n" for i, c in enumerate(cues, 1))


def to_vtt(cues: list[Cue]) -> str:
    body = "\n".join(f"{_ts(c.start_ms, '.')} --> {_ts(c.end_ms, '.')}\n{c.text}\n" for c in cues)
    return "WEBVTT\n\n" + body
