from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.highlight_logic import (
    CATEGORIES,
    DEFAULTS,
    Limits,
    RawCandidate,
    RawResult,
    audio_energy_score,
    build_messages,
    build_transcript,
    combine,
    dedupe,
    energy_reference,
    is_sentence_end,
    make_windows,
    merged_settings,
    overlap_ratio,
    padded_range,
    parse_segment_id,
    resolve,
    scene_alignment_score,
    speech_density_score,
)

pytestmark = pytest.mark.unit

LIM = Limits(min_ms=3000, max_ms=12000, snap_ms=6000, pause_ms=700, pad_before_ms=150, pad_after_ms=250)
WORD_MS = 400


def make_t(sentences: list[str], *, per_segment: int | None = None, gap_ms: int = 300, start_ms: int = 1000):
    """Every sentence becomes words 400 ms long. By default one sentence = one segment; per_segment splits by word count."""
    rows, words_all, t = [], [], start_ms
    for s in sentences:
        for w in s.split():
            words_all.append((w, t, t + WORD_MS))
            t += WORD_MS
        t += gap_ms
    chunks = (
        [words_all]
        if per_segment is None
        else [words_all[i : i + per_segment] for i in range(0, len(words_all), per_segment)]
    )
    if per_segment is None:  # one segment per sentence
        chunks, i = [], 0
        for s in sentences:
            n = len(s.split())
            chunks.append(words_all[i : i + n])
            i += n
    for idx, ch in enumerate(chunks):
        rows.append(
            (idx, ch[0][1], ch[-1][2], " ".join(w[0] for w in ch), [(j, w[0], w[1], w[2]) for j, w in enumerate(ch)])
        )
    return build_transcript(rows, t + 2000)


def window_of(t, lo=0, hi=None):
    from app.services.highlight_logic import Window

    ids = tuple(s.idx for s in t.segs[lo:hi])
    return Window(0, ids, (), 1)


def raw(a: int, b: int, **kw) -> RawCandidate:
    return RawCandidate(start_segment_id=a, end_segment_id=b, **kw)


# ------------------------------------------------------------------------------------------------ windows
def test_windows_cover_every_segment_overlap_and_respect_the_budget() -> None:
    t = make_t([f"this is sentence number {i} of the talk." for i in range(40)])
    ws = make_windows(t, budget_tokens=120, overlap=0.15, chars_per_token=3.0)
    assert len(ws) > 3
    assert {i for w in ws for i in w.seg_ids} == {s.idx for s in t.segs}  # nothing is ever left out
    assert all(w.tokens <= 120 or len(w.seg_ids) == 1 for w in ws)
    for a, b in zip(ws, ws[1:], strict=False):
        assert set(a.seg_ids) & set(b.seg_ids), "neighbouring windows must share segments"
        assert b.seg_ids[0] > a.seg_ids[0]  # always moves forward


def test_a_segment_bigger_than_the_budget_gets_its_own_window_and_terminates() -> None:
    t = make_t(["word " * 200 + "end.", "short one here."])
    ws = make_windows(t, budget_tokens=20, overlap=0.5, chars_per_token=3.0)
    assert [w.seg_ids for w in ws][0] == (0,) and {i for w in ws for i in w.seg_ids} == {0, 1}


def test_empty_transcript_has_no_windows() -> None:
    assert make_windows(build_transcript([], 0), 100, 0.15, 3.0) == []


def test_window_lines_carry_segment_ids_and_times_but_the_prompt_never_asks_for_text() -> None:
    t = make_t(["hello there world friend."])
    w = make_windows(t, 500, 0.15, 3.0)[0]
    assert w.lines[0].startswith("[s0 00:01] hello there")
    system = build_messages(w, prompt="funny moments", categories=["humour"], min_s=20, max_s=90, max_per_window=3)[0][
        1
    ]
    assert (
        "Never write times and never quote text" in system
        and "funny moments"
        in build_messages(w, prompt="funny moments", categories=[], min_s=1, max_s=2, max_per_window=1)[1][1]
    )


# ------------------------------------------------------------------------------------------------ the model's answer
@pytest.mark.parametrize(("value", "expected"), [(12, 12), ("12", 12), ("s12", 12), ("[s12]", 12), (" S7 ", 7)])
def test_segment_ids_are_parsed_leniently(value, expected) -> None:
    assert parse_segment_id(value) == expected


@pytest.mark.parametrize("bad", ["first", "12:30", "", None, True, 1.5])
def test_anything_that_is_not_a_segment_id_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        parse_segment_id(bad)


def test_raw_candidates_are_normalised_but_ids_are_never_defaulted() -> None:
    r = RawResult.model_validate(
        {
            "candidates": [
                {
                    "start_segment_id": "s2",
                    "end_segment_id": 5,
                    "category": "Surprising Fact",
                    "score": 150,
                    "standalone": -4,
                    "confidence": "70",
                }
            ]
        }
    )
    c = r.candidates[0]
    assert (c.start_segment_id, c.end_segment_id, c.category) == (2, 5, "surprising_fact")
    assert (c.score, c.standalone, c.confidence) == (100, 0, 70)
    assert RawCandidate(start_segment_id=1, end_segment_id=1, category="nonsense").category == "other"
    with pytest.raises(ValueError):
        RawResult.model_validate({"candidates": [{"end_segment_id": 3}]})  # no start id: invalid, not guessed


def test_every_category_is_known_and_other_exists() -> None:
    assert "other" in CATEGORIES and len(set(CATEGORIES)) == len(CATEGORIES)


# ------------------------------------------------------------------------------------------------ resolving ids to real words
SENT = [
    "The first idea is about how people learn new skills quickly.",  # 11 words = 4400 ms
    "It starts with small daily practice.",  # 6 words
    "Then you review what went wrong.",  # 6
    "Finally you teach it to somebody else.",  # 7
    "That is the whole method really.",  # 6
]


def test_timestamps_come_from_the_words_not_from_the_model() -> None:
    t = make_t(SENT)
    r = resolve(t, raw(1, 2), window_of(t), LIM)
    assert not isinstance(r, str)
    assert t.text_of(r.start_pos, r.end_pos) == "It starts with small daily practice. Then you review what went wrong."
    assert r.start_ms <= t.words[r.start_pos].start_ms and r.end_ms >= t.words[r.end_pos].end_ms
    assert (t.words[r.start_pos].start_ms - r.start_ms) <= LIM.pad_before_ms


def test_ids_the_model_was_not_shown_are_refused() -> None:
    t = make_t(SENT)
    w = window_of(t, 0, 3)  # the model saw segments 0-2 only
    assert resolve(t, raw(0, 4), w, LIM) == "unknown_segment"
    assert resolve(t, raw(99, 100), w, LIM) == "unknown_segment"  # invented
    assert resolve(t, raw(2, 1), window_of(t), LIM) == "reversed"


def test_a_start_in_the_middle_of_a_sentence_snaps_back_to_the_sentence_start() -> None:
    # segments of 5 words cut sentences in half, like real ASR segments do
    t = make_t(SENT, per_segment=5)
    seg = next(s for s in t.segs if not t.text_of(s.first, s.first).istitle() or t.words[s.first].text[0].islower())
    r = resolve(t, raw(seg.idx, seg.idx + 3), window_of(t), LIM)
    assert not isinstance(r, str)
    first_word = t.words[r.start_pos]
    assert first_word.text[0].isupper() or r.start_pos == 0
    assert is_sentence_end(t, r.end_pos, LIM.pause_ms)  # and it never ends mid-sentence


def test_too_long_is_trimmed_back_to_a_sentence_end_or_dropped() -> None:
    t = make_t(SENT)
    short = Limits(3000, 9000, 6000, 700, 150, 250)
    r = resolve(t, raw(0, 4), window_of(t), short)
    assert not isinstance(r, str)
    assert t.words[r.end_pos].text.endswith(".") and (t.words[r.end_pos].end_ms - t.words[r.start_pos].start_ms) <= 9000
    one_giant = make_t(["word " * 60 + "end."])
    assert resolve(one_giant, raw(0, 0), window_of(one_giant), Limits(3000, 9000, 1000, 700, 0, 0)) == "too_long"


def test_too_short_grows_over_following_sentences_or_is_dropped() -> None:
    t = make_t(SENT)
    r = resolve(t, raw(1, 1), window_of(t), Limits(7000, 20000, 6000, 700, 0, 0))
    assert not isinstance(r, str) and (t.words[r.end_pos].end_ms - t.words[r.start_pos].start_ms) >= 7000
    tiny = make_t(["Just one short line here."])
    assert resolve(tiny, raw(0, 0), window_of(tiny), Limits(20000, 90000, 6000, 700, 0, 0)) == "too_short"


def test_a_long_pause_counts_as_a_boundary_even_without_punctuation() -> None:
    t = make_t(["no punctuation here at all", "second thought begins now"], gap_ms=2000)
    assert is_sentence_end(t, 4, 700) and not is_sentence_end(t, 1, 700)


def test_padding_never_crosses_into_the_neighbouring_word_or_the_media_edges() -> None:
    t = make_t(["tight words follow.", "next sentence follows tight."], gap_ms=100)
    a, b = t.segs[1].first, t.segs[1].last
    s, e = padded_range(t, a, b, Limits(0, 99999, 0, 700, 500, 500))
    assert s >= t.words[a - 1].end_ms and s <= t.words[a].start_ms  # half of a 100 ms gap at most
    first = padded_range(t, 0, 2, Limits(0, 99999, 0, 700, 5000, 5000))
    assert first[0] == 500 and first[1] <= t.duration_ms  # half of the 1000 ms before the first word, not all of it
    assert padded_range(t, 0, 2, Limits(0, 99999, 0, 700, 5000, 5000))[0] >= 0


# ------------------------------------------------------------------------------------------------ scoring and ranking
def test_density_scene_and_energy_scores() -> None:
    t = make_t(["one two three four five six."])
    assert speech_density_score(t, 0, 5, target_wps=2.5) == pytest.approx(100.0)  # exactly 2.5 words per second
    assert speech_density_score(t, 0, 5, target_wps=10) == pytest.approx(25.0)
    assert scene_alignment_score(1000, 5000, [], 500) is None
    assert scene_alignment_score(1000, 5000, [1100, 9000], 500) == 50.0
    assert scene_alignment_score(1000, 5000, [1100, 4900], 500) == 100.0
    peaks = [[-10, 10]] * 50 + [[-100, 100]] * 50
    ref = energy_reference(peaks)
    assert ref == 200.0
    assert audio_energy_score(0, 4999, 10000, peaks, ref) == pytest.approx(10.0)
    assert audio_energy_score(5000, 9999, 10000, peaks, ref) == pytest.approx(100.0)
    assert audio_energy_score(0, 100, 10000, [], None) is None and energy_reference([[0, 0]] * 5) is None


def test_missing_components_are_left_out_and_the_rest_renormalised() -> None:
    w = {"llm": 0.5, "category": 0.1, "standalone": 0.15, "density": 0.05, "scene": 0.05, "energy": 0.15}
    full, detail = combine({"llm": 80, "category": 100, "standalone": 60, "density": 100, "scene": 50, "energy": 40}, w)
    assert full == pytest.approx(0.5 * 80 + 0.1 * 100 + 0.15 * 60 + 0.05 * 100 + 0.05 * 50 + 0.15 * 40, abs=0.01)
    assert detail["unavailable"] == []
    partial, d2 = combine(
        {"llm": 80, "category": None, "standalone": 60, "density": 100, "scene": None, "energy": None}, w
    )
    assert partial == pytest.approx((0.5 * 80 + 0.15 * 60 + 0.05 * 100) / 0.7, abs=0.01)
    assert d2["unavailable"] == ["category", "energy", "scene"] and sum(
        v["weight"] for k, v in d2.items() if k != "unavailable"
    ) == pytest.approx(1.0, abs=0.01)
    assert combine({"llm": None}, w)[0] == 0.0


@dataclass
class C:
    name: str
    start_ms: int
    end_ms: int
    final: float


def test_overlapping_candidates_keep_only_the_stronger_one() -> None:
    items = [
        C("weak", 1000, 11000, 40),
        C("strong", 5000, 15000, 90),
        C("elsewhere", 30000, 40000, 10),
        C("inside", 6000, 9000, 20),
    ]
    kept, dropped = dedupe(items, 0.3)
    assert [k.name for k in kept] == ["strong", "elsewhere"] and dropped == 2
    assert overlap_ratio((0, 10), (10, 20)) == 0.0 and overlap_ratio((0, 10), (2, 4)) == 1.0  # containment counts fully


def test_settings_merge_ignores_unknown_keys_and_bad_types() -> None:
    s = merged_settings({"min_seconds": 30, "max_seconds": "lots", "bogus": 1, "weights": {"llm": 0.9}})
    assert s["min_seconds"] == 30 and s["max_seconds"] == DEFAULTS["max_seconds"] and "bogus" not in s
    assert s["weights"]["llm"] == 0.9 and s["weights"]["energy"] == DEFAULTS["weights"]["energy"]
    assert (
        merged_settings(None) == merged_settings({}) and DEFAULTS["weights"]["llm"] == 0.50
    )  # defaults are not mutated
