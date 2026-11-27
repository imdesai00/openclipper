from __future__ import annotations

import pytest

from app.pipeline.asr_options import LANGUAGES, MODELS, normalize_language
from app.services.transcript_logic import (
    MAX_CUE_CHARS,
    MAX_CUE_MS,
    MIN_WORD_MS,
    W,
    align_edit,
    compute_cache_key,
    make_cues,
    norm,
    tidy,
    to_srt,
    to_vtt,
)

pytestmark = pytest.mark.unit

BASE = {
    "media_hash": "abc",
    "engine": "faster-whisper",
    "model": "small",
    "language": None,
    "vad": True,
    "loudnorm": True,
    "denoise": False,
}


def words(text: str, start: int = 0, step: int = 400) -> list[W]:
    return [W(t, start + i * step, start + (i + 1) * step - 50) for i, t in enumerate(text.split())]


# ------------------------------------------------------------------------------------------ cache key
def test_cache_key_is_stable_and_sensitive_to_every_input() -> None:
    k = compute_cache_key(**BASE)
    assert k == compute_cache_key(**BASE) and len(k) == 64
    for field, other in [("media_hash", "abd"), ("engine", "x"), ("model", "tiny"), ("language", "es"), ("vad", False),
                         ("loudnorm", False), ("denoise", True)]:  # fmt: skip
        assert compute_cache_key(**{**BASE, field: other}) != k, field


def test_cache_key_treats_auto_language_as_none() -> None:
    assert compute_cache_key(**BASE) == compute_cache_key(**{**BASE, "language": None})


# ------------------------------------------------------------------------------------------ languages
def test_language_normalisation() -> None:
    assert normalize_language(None) is None and normalize_language("") is None and normalize_language("auto") is None
    assert normalize_language(" ES ") == "es"
    with pytest.raises(ValueError):
        normalize_language("klingon")
    assert "en" in LANGUAGES and len(LANGUAGES) == 100


def test_models_are_data_with_required_fields() -> None:
    for name, m in MODELS.items():
        assert {"mb", "multilingual", "note"} <= set(m), name
    assert MODELS["tiny.en"]["multilingual"] is False and MODELS["small"]["multilingual"] is True


# ------------------------------------------------------------------------------------------ edit alignment
def test_unchanged_text_keeps_every_timing_exactly() -> None:
    old = words("the quick brown fox")
    new = align_edit(old, "the quick brown fox", 0, 1600)
    assert [(w.start_ms, w.end_ms) for w in new] == [(w.start_ms, w.end_ms) for w in old]


def test_case_and_punctuation_changes_keep_timings_but_take_new_text() -> None:
    old = words("the quick brown fox")
    new = align_edit(old, "The quick, brown fox.", 0, 1600)
    assert [w.text for w in new] == ["The", "quick,", "brown", "fox."]
    assert [(w.start_ms, w.end_ms) for w in new] == [(w.start_ms, w.end_ms) for w in old]


def test_replaced_word_takes_the_span_of_the_word_it_replaces() -> None:
    old = words("the quick brown fox")
    new = align_edit(old, "the quick red fox", 0, 1600)
    assert new[2].text == "red" and (new[2].start_ms, new[2].end_ms) == (old[2].start_ms, old[2].end_ms)
    assert new[3].start_ms == old[3].start_ms  # neighbours untouched


def test_inserted_words_fit_in_the_gap_and_stay_ordered() -> None:
    old = words("hello world", step=1000)
    new = align_edit(old, "hello big wide world", 0, 2000)
    assert [w.text for w in new] == ["hello", "big", "wide", "world"]
    assert all(a.end_ms <= b.start_ms for a, b in zip(new, new[1:], strict=False))
    assert new[0].start_ms == 0 and new[-1].end_ms <= 2000


def test_deleting_words_removes_them_and_keeps_the_rest() -> None:
    old = words("one two three four")
    new = align_edit(old, "one four", 0, 1600)
    assert [w.text for w in new] == ["one", "four"] and new[1].start_ms == old[3].start_ms


def test_empty_edit_is_refused() -> None:
    with pytest.raises(ValueError):
        align_edit(words("a b"), "   ", 0, 800)


@pytest.mark.parametrize(
    "text", ["x", "a b c d e f g h i j k l m n o p q r s t u v w x y z", "completely different sentence now"]
)
def test_aligned_words_are_always_valid(text: str) -> None:
    old = words("the quick brown fox jumps")
    new = align_edit(old, text, 0, 2000)
    assert len(new) == len(text.split())
    assert all(0 <= w.start_ms <= w.end_ms <= 2000 for w in new)
    assert all(a.end_ms <= b.start_ms for a, b in zip(new, new[1:], strict=False))


def test_tidy_enforces_order_bounds_and_minimum_length() -> None:
    out = tidy([W("a", 500, 500), W("b", 100, 200), W("c", 5000, 6000)], 0, 1000)
    assert all(0 <= w.start_ms <= w.end_ms <= 1000 for w in out)
    assert all(a.end_ms <= b.start_ms for a, b in zip(out, out[1:], strict=False))
    assert out[0].end_ms - out[0].start_ms >= MIN_WORD_MS


def test_norm_ignores_case_and_punctuation_but_keeps_apostrophes_and_accents() -> None:
    assert norm("Don't!") == "don't" and norm("¿Cómo?") == "cómo"


# ------------------------------------------------------------------------------------------ subtitles
def test_cues_never_exceed_limits_or_overlap() -> None:
    long = words(" ".join(["wordy"] * 60), step=300)
    cues = make_cues([long])
    assert len(cues) > 1
    for c in cues:
        assert (
            all(len(line) <= 42 + 6 for line in c.text.split("\n")) and len(c.text.replace("\n", " ")) <= MAX_CUE_CHARS
        )
        assert c.end_ms - c.start_ms <= MAX_CUE_MS + 300
    assert all(a.end_ms <= b.start_ms for a, b in zip(cues, cues[1:], strict=False))


def test_a_long_pause_splits_a_cue_and_segments_never_merge() -> None:
    a = [W("hello", 0, 400), W("there", 3000, 3400)]
    assert len(make_cues([a])) == 2
    assert len(make_cues([[W("one", 0, 300)], [W("two", 300, 600)]])) == 2


def test_srt_and_vtt_formats() -> None:
    cues = make_cues([[W("Hello", 1000, 1400), W("world", 1400, 3_723_456)]])
    srt, vtt = to_srt(cues), to_vtt(cues)
    assert srt.startswith("1\n00:00:01,000 --> ")
    assert "01:02:03,456" in srt
    assert vtt.startswith("WEBVTT\n\n00:00:01.000 --> ") and "01:02:03.456" in vtt
