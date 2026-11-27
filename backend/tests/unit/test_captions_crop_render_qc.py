from __future__ import annotations

import re

import pytest

from app.services import caption_engine as ce
from app.services import clip_qc as qc
from app.services import crop_logic as cl
from app.services import render_plan as rp

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------------------------------------ caption style
def test_the_default_style_is_valid_and_every_builtin_template_validates() -> None:
    assert ce.CaptionStyle().schema_version == 1
    for name, over in ce.BUILTIN_TEMPLATES.items():
        assert ce.merge_style({}, over), name


@pytest.mark.parametrize(
    "bad",
    [
        {"font_family": "Comic Sans"},
        {"color": "red"},
        {"font_size_pct": 40},
        {"animation": "spin"},
        {"max_lines": 9},
        {"nope": 1},
    ],
)
def test_bad_styles_are_refused_not_silently_fixed(bad) -> None:
    with pytest.raises(ValueError):
        ce.validate_style({**ce.CaptionStyle().model_dump(), **bad})


def test_a_clips_overrides_win_and_a_partial_box_override_keeps_the_rest() -> None:
    base = {"box": {"enabled": True, "color": "#112233", "opacity": 0.5}, "font_size_pct": 4.0}
    s = ce.merge_style(base, {"box": {"opacity": 0.9}, "color": "#ff0000"})
    assert (s.box.enabled, s.box.color, s.box.opacity, s.color, s.font_size_pct) == (
        True,
        "#112233",
        0.9,
        "#FF0000",
        4.0,
    )


# ------------------------------------------------------------------------------------------------ cues
def W(text, s, e):  # noqa: N802
    return (text, s, e)


def test_only_words_inside_the_clip_are_captioned_and_times_are_relative() -> None:
    words = [W("before", 900, 1000), W("hello", 1000, 1300), W("world.", 1300, 1700), W("after", 2500, 2800)]
    cues = ce.build_cues(words, 1000, 2000, ce.CaptionStyle())
    assert [w.text for c in cues for w in c.words] == ["hello", "world."]
    assert cues[0].words[0].start_ms == 0 and cues[0].words[1].start_ms == 300


def test_cues_break_at_long_pauses_sentences_and_line_limits_and_never_overlap() -> None:
    style = ce.CaptionStyle(max_chars_per_line=12, max_lines=2, max_words_per_cue=10)
    words, t = [], 0
    for i in range(14):
        words.append(W(f"word{i:02d}", t, t + 300))
        t += 320
    words += [W("after", t + 1500, t + 1800)]  # a 1.2 s pause
    cues = ce.build_cues(words, 0, t + 3000, style)
    assert len(cues) >= 4
    for c in cues:
        assert len(c.lines) <= 2 and all(len(" ".join(w.text for w in ln)) <= 12 for ln in c.lines)
    assert all(a.end_ms < b.start_ms for a, b in zip(cues, cues[1:], strict=False))
    assert cues[-1].words[0].text == "after"  # the pause forced a new cue


def test_a_sentence_end_starts_a_new_cue_once_there_are_enough_words() -> None:
    words = [W("One", 0, 200), W("two", 200, 400), W("three.", 400, 700), W("Four", 750, 900), W("five", 900, 1100)]
    cues = ce.build_cues(words, 0, 2000, ce.CaptionStyle())
    assert [[w.text for w in c.words] for c in cues] == [["One", "two", "three."], ["Four", "five"]]


def test_caption_words_match_the_transcript_exactly() -> None:
    words = [W("hello", 100, 400), W("there", 400, 800)]
    cues = ce.build_cues(words, 0, 1000, ce.CaptionStyle())
    assert ce.max_word_drift_ms(cues, words, 0) == 0
    cues[0].lines[0][0] = ce.CapWord("hello", 180, 400)  # 80 ms off
    assert ce.max_word_drift_ms(cues, words, 0) == 80


# ------------------------------------------------------------------------------------------------ ASS
def test_ass_time_and_colour_formats() -> None:
    assert ce.ass_time(0) == "0:00:00.00" and ce.ass_time(3_723_450) == "1:02:03.45" and ce.ass_time(-5) == "0:00:00.00"
    assert ce.ass_color("#FF8000") == "&H000080FF"  # ASS is blue-green-red
    assert ce.ass_color("#000000", 0.6) == "&H66000000"  # alpha 00 opaque ... FF transparent


def test_ass_document_has_the_requested_size_style_and_one_event_per_cue() -> None:
    style = ce.CaptionStyle(animation="none", font_family="DejaVu Sans", position="bottom", margin_v_pct=10)
    cues = ce.build_cues([W("hi", 0, 300), W("there", 300, 700)], 0, 1000, style)
    doc = ce.compile_ass(style, cues, 1080, 1920)
    assert "PlayResX: 1080" in doc and "PlayResY: 1920" in doc and "WrapStyle: 2" in doc
    st = next(ln for ln in doc.splitlines() if ln.startswith("Style: Default"))
    f = st.removeprefix("Style: ").split(",")
    assert f[1] == "DejaVu Sans" and f[2] == str(round(0.046 * 1920 * ce.ASS_SIZE_FACTOR["DejaVu Sans"]))
    assert f[18] == "2" and f[21] == "192"  # bottom-centre anchor, 10% of 1920 px from the edge
    assert doc.count("Dialogue:") == 1 and "hi there" in doc


def test_braces_and_backslashes_in_words_cannot_inject_ass_tags() -> None:
    style = ce.CaptionStyle(animation="none")
    cues = ce.build_cues([W("{\\fs500}boom", 0, 400)], 0, 1000, style)
    doc = ce.compile_ass(style, cues, 1080, 1920)
    assert "\\fs500" not in doc and "boom" in doc


@pytest.mark.parametrize(
    ("animation", "expect"),
    [("karaoke", r"\kf"), ("highlight", r"\c&H"), ("pop", r"\fscx118")],
)
def test_animated_styles_emit_their_tags(animation, expect) -> None:
    style = ce.CaptionStyle(animation=animation)
    cues = ce.build_cues([W("a", 0, 300), W("b", 300, 600), W("c", 600, 900)], 0, 1000, style)
    doc = ce.compile_ass(style, cues, 1080, 1920)
    assert expect in doc
    if animation != "karaoke":
        assert doc.count("Dialogue:") == 3  # one event per spoken word, the active word coloured
    else:
        assert doc.count("Dialogue:") == 1 and doc.count(r"\kf") == 3


def test_keywords_get_their_own_colour_and_uppercase_applies() -> None:
    style = ce.CaptionStyle(animation="none", keywords=["Money"], uppercase=True)
    cues = ce.build_cues([W("make", 0, 200), W("money,", 200, 500)], 0, 1000, style)
    doc = ce.compile_ass(style, cues, 1080, 1920)
    assert "MAKE" in doc and f"{{\\c{ce.ass_color(style.keyword_color)}}}MONEY," in doc


# ------------------------------------------------------------------------------------------------ CSS and ASS agree
@pytest.mark.parametrize("font", ce.FONTS)
@pytest.mark.parametrize("position", ["bottom", "middle", "top"])
@pytest.mark.parametrize("size", [(1080, 1920), (360, 640), (1920, 1080)])
def test_css_and_ass_describe_the_same_caption(font, position, size) -> None:
    w, h = size
    style = ce.CaptionStyle(
        font_family=font, position=position, font_size_pct=5.0, margin_v_pct=9, outline_pct=0.3, bold=True
    )
    css = ce.compile_css(style, w, h)
    cues = ce.build_cues([W("hello", 0, 300), W("world", 300, 700)], 0, 1000, style)
    ass = ce.compile_ass(style, cues, w, h)
    st = next(ln for ln in ass.splitlines() if ln.startswith("Style: Default")).removeprefix("Style: ").split(",")
    # same font, same size once libass's different sizing is accounted for, same outline, same margins, same anchor
    assert st[1] == font and css["text"]["font-family"].startswith(f"'{font}'")
    css_px = float(css["text"]["font-size"].removesuffix("px"))
    assert abs(int(st[2]) - css_px * ce.ASS_SIZE_FACTOR[font]) <= 1
    outline_css = float(css["text"]["-webkit-text-stroke"].split("px")[0]) / 2
    assert abs(int(st[16]) - outline_css) <= 1
    ml, mr, mv = int(st[19]), int(st[20]), int(st[21])
    assert (
        (ml, mr) == (round(w * 0.06), round(w * 0.06))
        and css["container"]["left"] == f"{ml}px"
        and css["container"]["right"] == f"{mr}px"
    )
    anchor = {"bottom": 2, "middle": 5, "top": 8}[position]
    assert int(st[18]) == anchor
    edge = {"bottom": "bottom", "top": "top"}.get(position)
    if edge:
        assert css["container"][edge] == f"{mv}px"
    else:
        assert css["container"]["top"] == "50%"
    assert st[3] == ce.ass_color(style.color) and css["text"]["color"] == style.color


def test_the_boxed_style_uses_an_opaque_box_in_both_outputs() -> None:
    style = ce.merge_style({}, ce.BUILTIN_TEMPLATES["Boxed"])
    css = ce.compile_css(style, 1080, 1920)
    cues = ce.build_cues([W("hi", 0, 300)], 0, 1000, style)
    st = next(
        ln for ln in ce.compile_ass(style, cues, 1080, 1920).splitlines() if ln.startswith("Style: Default")
    ).split(",")
    assert (
        css["text"]["background"].startswith("rgba(0, 0, 0, 0.65")
        and st[15] == "3"
        and "-webkit-text-stroke" not in css["text"]
    )


def test_the_caption_block_stays_inside_the_picture_for_every_position() -> None:
    for pos in ("bottom", "middle", "top"):
        left, top, right, bottom = ce.caption_box(ce.CaptionStyle(position=pos), 1080, 1920, 2)
        assert 0 <= left < right <= 1080 and 0 <= top < bottom <= 1920


# ------------------------------------------------------------------------------------------------ crop
def _eval(expr: str, t: float) -> float:
    py = re.sub(r"\bif\(", "_if(", re.sub(r"\blt\(", "_lt(", expr))
    return float(eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "t": t}))  # noqa: S307


def test_the_largest_window_of_each_aspect_fits_and_zoom_shrinks_it() -> None:
    assert cl.window_size(1920, 1080, "9:16", 1.0) == (606, 1080)
    assert cl.window_size(1920, 1080, "1:1", 1.0) == (1080, 1080)
    assert cl.window_size(1920, 1080, "16:9", 1.0) == (1920, 1080)
    assert cl.window_size(1920, 1080, "9:16", 2.0) == (303 // 2 * 2, 540)
    assert all(v % 2 == 0 for v in cl.window_size(1281, 721, "9:16", 1.3))


def test_pan_interpolates_between_keyframes_and_holds_outside_them() -> None:
    spec = cl.normalize_crop(
        {"keyframes": [{"t_ms": 1000, "cx": 0.2, "cy": 0.5}, {"t_ms": 3000, "cx": 0.8, "cy": 0.5}]}, 5000
    )
    assert (
        cl.pan_at(spec, 0) == (0.2, 0.5)
        and cl.pan_at(spec, 2000)[0] == pytest.approx(0.5)
        and cl.pan_at(spec, 4000) == (0.8, 0.5)
    )
    assert cl.pan_at(cl.CropSpec(), 123) == (0.5, 0.5)


def test_the_window_never_leaves_the_picture() -> None:
    spec = cl.normalize_crop(
        {"keyframes": [{"t_ms": 0, "cx": 0.0, "cy": 0.0}, {"t_ms": 1000, "cx": 1.0, "cy": 1.0}]}, 1000
    )
    for t in (0, 250, 500, 1000):
        x, y, w, h = cl.window_at(spec, 1920, 1080, "9:16", t)
        assert 0 <= x <= 1920 - w and 0 <= y <= 1080 - h


@pytest.mark.parametrize("t_ms", [0, 400, 1000, 1700, 2500, 3000, 4200])
def test_the_ffmpeg_expression_agrees_with_the_python_window(t_ms) -> None:
    spec = cl.normalize_crop(
        {
            "zoom": 1.5,
            "keyframes": [
                {"t_ms": 500, "cx": 0.3, "cy": 0.4},
                {"t_ms": 2000, "cx": 0.7, "cy": 0.6},
                {"t_ms": 3000, "cx": 0.5, "cy": 0.5},
            ],
        },
        5000,
    )
    w, h, xe, ye = cl.crop_exprs(spec, 1920, 1080, "9:16")
    x, y, ww, hh = cl.window_at(spec, 1920, 1080, "9:16", t_ms)
    assert (w, h) == (ww, hh)
    assert _eval(xe, t_ms / 1000) == pytest.approx(x, abs=1.5) and _eval(ye, t_ms / 1000) == pytest.approx(y, abs=1.5)


@pytest.mark.parametrize(
    "bad",
    [
        {"mode": "stretch"},
        {"zoom": 0.5},
        {"zoom": 9},
        {"zoom": "x"},
        {"keyframes": [{"t_ms": 99999, "cx": 0.5, "cy": 0.5}]},
        {"keyframes": [{"t_ms": 0, "cx": 1.5, "cy": 0.5}]},
        {"keyframes": [{"cx": 0.5}]},
        {"keyframes": [{"t_ms": i, "cx": 0.5, "cy": 0.5} for i in range(30)]},
    ],
)
def test_bad_crops_are_refused(bad) -> None:
    with pytest.raises(cl.CropError):
        cl.normalize_crop(bad, 5000)


def test_duplicate_keyframe_times_keep_the_last_and_order_is_fixed() -> None:
    spec = cl.normalize_crop(
        {
            "keyframes": [
                {"t_ms": 900, "cx": 0.9, "cy": 0.5},
                {"t_ms": 100, "cx": 0.1, "cy": 0.5},
                {"t_ms": 100, "cx": 0.2, "cy": 0.5},
            ]
        },
        1000,
    )
    assert [(k.t_ms, k.cx) for k in spec.keyframes] == [(100, 0.2), (900, 0.9)]


# ------------------------------------------------------------------------------------------------ render plan
def spec(**kw) -> rp.RenderSpec:
    base = dict(
        kind="final",
        aspect="9:16",
        src_w=1920,
        src_h=1080,
        has_video=True,
        has_audio=True,
        start_ms=12_000,
        end_ms=42_000,
        crop=cl.CropSpec(),
        background={"type": "blur"},
        gain_db=0.0,
        mute=False,
        ass_path="/tmp/oc x/captions.ass",
    )
    base.update(kw)
    return rp.RenderSpec(**base)  # type: ignore[arg-type]


def test_output_sizes() -> None:
    assert rp.output_size("final", "9:16") == (1080, 1920) and rp.output_size("final", "16:9") == (1920, 1080)
    assert rp.output_size("preview", "9:16") == (360, 640) and rp.output_size("preview", "1:1") == (640, 640)


def test_a_fill_render_seeks_on_the_input_crops_scales_and_burns_captions() -> None:
    a = rp.build_args(spec(), "/in/original.mp4", "/out/clip.mp4")
    assert a[:6] == ["-ss", "12.000", "-t", "30.000", "-i", "/in/original.mp4"]  # input-side seek, exact length
    g = a[a.index("-filter_complex") + 1]
    assert (
        "crop=w=606:h=1080" in g
        and "scale=1080:1920" in g
        and "ass=filename='/tmp/oc x/captions.ass'" in g
        and g.count("[v]") == 1
    )
    assert a[-1] == "/out/clip.mp4" and "libx264" in a and "+faststart" in a and all(isinstance(x, str) for x in a)


def test_the_ass_path_is_escaped_for_the_filter_syntax() -> None:
    g = rp.build_args(spec(ass_path="/tmp/a:b'c,d.ass"), "i", "o")
    assert "filename='/tmp/a\\:b\\'c\\,d.ass'" in g[g.index("-filter_complex") + 1]


def test_fit_mode_with_a_blurred_or_solid_background() -> None:
    blur = rp.build_args(spec(crop=cl.CropSpec(mode="fit")), "i", "o")
    gb = blur[blur.index("-filter_complex") + 1]
    assert "gblur=sigma=" in gb and "overlay=(W-w)/2:(H-h)/2" in gb and "lavfi" not in blur
    solid = rp.build_args(
        spec(crop=cl.CropSpec(mode="fit"), background={"type": "solid", "color": "#102030"}), "i", "o"
    )
    assert (
        "color=c=0x102030:s=1080x1920:r=30" in solid and "[1:v][fg]overlay" in solid[solid.index("-filter_complex") + 1]
    )


def test_an_audio_only_source_becomes_a_waveform_on_a_background() -> None:
    a = rp.build_args(spec(has_video=False, src_w=None, src_h=None, crop=cl.CropSpec()), "i.m4a", "o.mp4")
    g = a[a.index("-filter_complex") + 1]
    assert "showwaves=s=1080x480" in g and "asplit=2[a_wave][a_src]" in g and "[a_src]" in g and "ass=filename" in g
    assert any(x.startswith("color=c=0x1B1B2F:s=1080x1920") for x in a)


def test_a_source_without_sound_gets_a_silent_track_not_a_missing_one() -> None:
    a = rp.build_args(spec(has_audio=False), "i", "o")
    assert "anullsrc=r=48000:cl=stereo" in a and "[a]" in a[a.index("-filter_complex") + 1]
    assert spec(has_audio=False).audio_silent_by_design and spec(mute=True).audio_silent_by_design


def test_gain_mute_and_second_pass_loudness() -> None:
    measured = {"input_i": -23.4, "input_tp": -4.1, "input_lra": 6.2, "input_thresh": -33.9, "target_offset": 0.3}
    g = rp.build_args(spec(gain_db=3.0, measured=measured), "i", "o")
    graph = g[g.index("-filter_complex") + 1]
    assert (
        "volume=3dB" in graph and "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-23.40" in graph and "linear=true" in graph
    )
    muted = rp.build_args(spec(mute=True, measured=measured), "i", "o")
    mg = muted[muted.index("-filter_complex") + 1]
    assert "volume=0dB" in mg and "loudnorm" not in mg
    prev = rp.build_args(spec(kind="preview", measured=measured), "i", "o")
    assert (
        "loudnorm" not in prev[prev.index("-filter_complex") + 1]
        and "ultrafast" in prev
        and "scale=360:640" in prev[prev.index("-filter_complex") + 1]
    )


def test_first_pass_measurement_args_and_parsers() -> None:
    a = rp.measure_args(spec(gain_db=2.0), "in.mp4")
    assert (
        a[:4] == ["-ss", "12.000", "-t", "30.000"]
        and "volume=2dB,loudnorm=I=-14" in a[a.index("-af") + 1]
        and a[-3:] == ["-f", "null", "-"]
    )
    sample = 'noise\n{\n\t"input_i" : "-23.41",\n\t"input_tp" : "-4.11",\n\t"input_lra" : "6.20",\n\t"input_thresh" : "-33.90",\n\t"output_i" : "-14.5",\n\t"target_offset" : "0.31"\n}\n'
    m = rp.parse_loudnorm(sample)
    assert m["input_i"] == -23.41 and m["target_offset"] == 0.31
    assert rp.parse_loudnorm(sample.replace("-23.41", "-inf"))["input_i"] == -99.0
    with pytest.raises(ValueError):
        rp.parse_loudnorm("nothing here")
    assert rp.parse_integrated_lufs("  I:         -14.2 LUFS\n ... Summary:\n  I:         -13.9 LUFS\n") == -13.9
    assert rp.sum_black("black_start:0 black_end:1 black_duration:1.5\nblack_duration:0.5") == 2.0
    assert rp.sum_silence("silence_duration: 2.25") == 2.25


# ------------------------------------------------------------------------------------------------ QC
def meas(**kw) -> qc.Measurements:
    base = dict(
        kind="final",
        expected_w=1080,
        expected_h=1920,
        expected_duration_s=30.0,
        has_video_stream=True,
        has_audio_stream=True,
        width=1080,
        height=1920,
        video_duration_s=30.0,
        audio_duration_s=30.02,
        container_duration_s=30.03,
        size_bytes=5_000_000,
        audio_expected_silent=False,
        source_is_audio_only=False,
        loudness_lufs=-14.3,
        black_s=0.0,
        silent_s=1.0,
        caption_box=(65, 1500, 1015, 1700),
        captions_expected=True,
        caption_drift_ms=0,
    )
    base.update(kw)
    return qc.Measurements(**base)  # type: ignore[arg-type]


def status(m: qc.Measurements) -> str:
    return str(qc.evaluate(m)["status"])


def failed(m: qc.Measurements) -> set[str]:
    return {c["check"] for c in qc.evaluate(m)["checks"] if c["severity"] == "fail"}


def test_a_good_render_passes_every_check() -> None:
    r = qc.evaluate(meas())
    assert r["status"] == "pass" and {c["check"] for c in r["checks"]} >= {
        "video_stream",
        "dimensions",
        "duration",
        "av_sync",
        "loudness",
        "caption_bounds",
    }


def test_each_kind_of_damage_is_caught_and_named() -> None:
    assert failed(meas(has_video_stream=False)) == {"video_stream"}
    assert failed(meas(has_audio_stream=False, audio_duration_s=None)) == {"audio_stream"}
    assert failed(meas(width=720, height=1280)) == {"dimensions"}
    assert failed(meas(container_duration_s=12.0, video_duration_s=12.0, audio_duration_s=12.0)) == {"duration"}
    assert failed(meas(audio_duration_s=31.0)) == {"av_sync"}
    assert failed(meas(size_bytes=0)) == {"file"}
    assert failed(meas(black_s=29.0)) == {"black_frames"}
    assert failed(meas(silent_s=30.0)) == {"silence"}
    assert failed(meas(caption_box=(-5, 1500, 1015, 1700))) == {"caption_bounds"}
    assert failed(meas(caption_drift_ms=250)) == {"caption_sync"}


def test_warnings_do_not_fail_and_intent_is_respected() -> None:
    assert (
        status(meas(black_s=4.0)) == "warn"
        and status(meas(silent_s=18.0)) == "warn"
        and status(meas(loudness_lufs=-19.0)) == "warn"
    )
    assert status(meas(audio_expected_silent=True, silent_s=30.0, loudness_lufs=None)) == "pass"  # muted on purpose
    assert status(meas(source_is_audio_only=True, black_s=30.0)) == "pass"  # no picture to be black
    assert not any(c["check"] == "loudness" for c in qc.evaluate(meas(kind="preview"))["checks"])
    assert (
        status(meas(container_duration_s=30.3, video_duration_s=30.3, audio_duration_s=30.3)) == "pass"
    )  # within tolerance
