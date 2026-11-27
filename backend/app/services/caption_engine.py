"""Captions: ONE style schema, compiled to CSS (editor preview) and to ASS (the render). Pure: no I/O.

SPEC architecture rule 7: the preview and the final render must come from the same definition. Everything that
decides how a caption looks lives in `CaptionStyle`. `compile_css` and `compile_ass` are two readings of it, and the
tests compare their numbers (font size, margins, anchor, colours) instead of trusting that they agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

# Fonts the render image ships (fonts-liberation, fonts-dejavu-core). A font that is not here would silently fall
# back to whatever libass finds, so a template may only name one of these.
FONTS = ("Liberation Sans", "Liberation Serif", "DejaVu Sans", "DejaVu Sans Mono")

# libass makes the font's whole line cell equal to the requested size, a browser makes the em equal to it, so the same
# number gives smaller letters in a render. MEASURED on 2026-11-27 (ffmpeg 6.1.1 libass vs Chromium, ink width of
# "HHHH" at 200 px): the factor that makes ASS letters as large as CSS letters. An integration test re-measures it.
ASS_SIZE_FACTOR: dict[str, float] = {
    "Liberation Sans": 1.12,
    "Liberation Serif": 1.108,
    "DejaVu Sans": 1.167,
    "DejaVu Sans Mono": 1.169,
}

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
Animation = Literal["none", "highlight", "karaoke", "pop"]
Position = Literal["bottom", "middle", "top"]


class Box(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt setting is an error, not a silent no-op

    enabled: bool = False
    color: str = "#000000"
    opacity: float = Field(default=0.6, ge=0, le=1)
    padding_pct: float = Field(default=0.8, ge=0, le=5)  # of the output height

    @field_validator("color")
    @classmethod
    def _c(cls, v: str) -> str:
        return _color(v)


def _color(v: str) -> str:
    if not _HEX.match(v):
        raise ValueError("must be a colour like #FFFFFF")
    return v.upper()


class CaptionStyle(BaseModel):
    """Every length is a percentage of the OUTPUT HEIGHT, so one template looks the same at 360p, 1080p and 1920p."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    font_family: str = "Liberation Sans"
    font_size_pct: float = Field(default=4.6, ge=1.5, le=12)
    bold: bool = True
    italic: bool = False
    uppercase: bool = False
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_pct: float = Field(default=0.25, ge=0, le=1.5)
    shadow_pct: float = Field(default=0.0, ge=0, le=1.5)
    box: Box = Field(default_factory=Box)
    position: Position = "bottom"
    margin_v_pct: float = Field(default=12.0, ge=0, le=40)  # distance from the top or bottom edge
    safe_left_pct: float = Field(default=6.0, ge=0, le=30)  # of the output WIDTH
    safe_right_pct: float = Field(default=6.0, ge=0, le=30)
    max_chars_per_line: int = Field(default=24, ge=8, le=60)
    max_lines: int = Field(default=2, ge=1, le=3)
    max_words_per_cue: int = Field(default=7, ge=1, le=14)
    animation: Animation = "highlight"
    highlight_color: str = "#FFD400"
    keywords: list[str] = Field(default_factory=list, max_length=30)  # words shown in keyword_color
    keyword_color: str = "#FF4D4D"

    @field_validator("font_family")
    @classmethod
    def _font(cls, v: str) -> str:
        if v not in FONTS:
            raise ValueError(f"font must be one of: {', '.join(FONTS)}")
        return v

    @field_validator("color", "outline_color", "highlight_color", "keyword_color")
    @classmethod
    def _colors(cls, v: str) -> str:
        return _color(v)

    @field_validator("keywords")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        return [k.strip().lower() for k in v if k.strip()][:30]


BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "Clean": {"animation": "none", "font_size_pct": 4.2, "outline_pct": 0.3},
    "Bold highlight": {"animation": "highlight", "uppercase": True, "font_size_pct": 5.0, "highlight_color": "#FFD400"},
    "Karaoke": {"animation": "karaoke", "font_size_pct": 4.8, "highlight_color": "#00E5FF"},
    "Pop": {"animation": "pop", "uppercase": True, "font_size_pct": 5.2, "highlight_color": "#FF4D8D"},
    "Boxed": {
        "animation": "none",
        "font_size_pct": 4.0,
        "outline_pct": 0.0,
        "box": {"enabled": True, "color": "#000000", "opacity": 0.65, "padding_pct": 0.9},
    },
}


def validate_style(raw: dict[str, Any]) -> CaptionStyle:
    return CaptionStyle.model_validate(raw)


def merge_style(base: dict[str, Any], overrides: dict[str, Any] | None) -> CaptionStyle:
    """A template plus the clip's own changes. Unknown override keys are an error, never silently ignored."""
    ov = overrides or {}
    merged = {**base, **ov}
    if isinstance(ov.get("box"), dict):  # a partial box override keeps the template's other box settings
        merged["box"] = {**(base.get("box") or {}), **ov["box"]}
    return CaptionStyle.model_validate(merged)


# ----------------------------------------------------------------------------------------------- cues
@dataclass(frozen=True)
class CapWord:
    text: str
    start_ms: int  # relative to the start of the clip
    end_ms: int


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    lines: list[list[CapWord]]

    @property
    def words(self) -> list[CapWord]:
        return [w for line in self.lines for w in line]


_TERMINAL = ".?!…"
PAUSE_BREAK_MS = 700
MIN_CUE_MS = 120
HOLD_MS = 250


def _wrap(words: list[CapWord], max_chars: int) -> list[list[CapWord]]:
    lines: list[list[CapWord]] = [[]]
    used = 0
    for w in words:
        add = len(w.text) + (1 if lines[-1] else 0)
        if lines[-1] and used + add > max_chars:
            lines.append([])
            used = 0
            add = len(w.text)
        lines[-1].append(w)
        used += add
    return lines


def build_cues(
    words: list[tuple[str, int, int]], clip_start_ms: int, clip_end_ms: int, style: CaptionStyle
) -> list[Cue]:
    """Words in ABSOLUTE source time -> readable cues in time relative to the clip.

    Only words inside the clip are used (a word cut by the trim is dropped, not shown half-spoken). Cues break at long
    pauses, after a sentence when there are enough words, and whenever the wrapped text would need more lines than
    allowed. Cues never overlap."""
    inside = [
        CapWord(t.strip(), s - clip_start_ms, e - clip_start_ms)
        for t, s, e in words
        if t.strip() and s >= clip_start_ms - 40 and e <= clip_end_ms + 40
    ]
    inside = [CapWord(w.text, max(0, w.start_ms), max(w.end_ms, max(0, w.start_ms) + 20)) for w in inside]
    groups: list[list[CapWord]] = []
    cur: list[CapWord] = []
    for w in inside:
        if cur:
            gap = w.start_ms - cur[-1].end_ms
            full = len(cur) >= style.max_words_per_cue
            ended = cur[-1].text[-1] in _TERMINAL and len(cur) >= 3
            too_big = len(_wrap([*cur, w], style.max_chars_per_line)) > style.max_lines
            if gap >= PAUSE_BREAK_MS or full or ended or too_big:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    cues: list[Cue] = []
    for i, g in enumerate(groups):
        start = g[0].start_ms
        spoken_end = max(g[-1].end_ms, start + MIN_CUE_MS)
        nxt = groups[i + 1][0].start_ms if i + 1 < len(groups) else None
        end = (
            spoken_end + HOLD_MS if nxt is None else min(spoken_end + HOLD_MS, nxt - 1)
        )  # held a moment, never overlapping
        cues.append(Cue(start, max(end, start + 1), _wrap(g, style.max_chars_per_line)))
    return cues


# ----------------------------------------------------------------------------------- geometry shared by CSS and ASS
def px(style_pct: float, height: int) -> float:
    return style_pct / 100.0 * height


def font_px(style: CaptionStyle, height: int) -> float:
    """The size in CSS pixels. This is the number a person would call 'the font size'."""
    return round(px(style.font_size_pct, height), 2)


def ass_font_size(style: CaptionStyle, height: int) -> int:
    return round(font_px(style, height) * ASS_SIZE_FACTOR[style.font_family])


def margins(style: CaptionStyle, width: int, height: int) -> tuple[int, int, int]:
    return (
        round(width * style.safe_left_pct / 100),
        round(width * style.safe_right_pct / 100),
        round(px(style.margin_v_pct, height)),
    )


def caption_box(style: CaptionStyle, width: int, height: int, n_lines: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the caption block, in output pixels. Used for the safe-area check in QC."""
    ml, mr, mv = margins(style, width, height)
    line_h = font_px(style, height) * 1.2
    block = round(n_lines * line_h + 2 * px(style.box.padding_pct, height if style.box.enabled else 0))
    if style.position == "top":
        top = mv
    elif style.position == "middle":
        top = round((height - block) / 2)
    else:
        top = height - mv - block
    return ml, top, width - mr, top + block


# ----------------------------------------------------------------------------------------------- CSS
def _rgba(hex_color: str, opacity: float = 1.0) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {opacity:g})"


def compile_css(style: CaptionStyle, width: int, height: int) -> dict[str, Any]:
    """CSS for a preview frame of `width` x `height` pixels. The editor scales it to the size it shows the frame at."""
    ml, mr, mv = margins(style, width, height)
    outline = px(style.outline_pct, height)
    shadow = px(style.shadow_pct, height)
    box_pad = px(style.box.padding_pct, height)
    container: dict[str, str] = {
        "position": "absolute",
        "left": f"{ml}px",
        "right": f"{mr}px",
        "text-align": "center",
    }
    if style.position == "top":
        container["top"] = f"{mv}px"
    elif style.position == "middle":
        container.update({"top": "50%", "transform": "translateY(-50%)"})
    else:
        container["bottom"] = f"{mv}px"
    text: dict[str, str] = {
        "font-family": f"'{style.font_family}', sans-serif",
        "font-size": f"{font_px(style, height)}px",
        "font-weight": "700" if style.bold else "400",
        "font-style": "italic" if style.italic else "normal",
        "text-transform": "uppercase" if style.uppercase else "none",
        "color": style.color,
        "line-height": "1.2",
    }
    if style.box.enabled:
        text["background"] = _rgba(style.box.color, style.box.opacity)
        text["padding"] = f"{box_pad:.2f}px"
        text["display"] = "inline-block"
    elif outline > 0:
        # a CSS stroke is centred on the glyph edge, so double it and paint it under the fill to get an outside outline
        text["-webkit-text-stroke"] = f"{outline * 2:.2f}px {style.outline_color}"
        text["paint-order"] = "stroke fill"
    if shadow > 0 and not style.box.enabled:
        text["text-shadow"] = f"{shadow:.2f}px {shadow:.2f}px 0 {style.outline_color}"
    return {
        "container": container,
        "text": text,
        "active_color": style.highlight_color,
        "keyword_color": style.keyword_color,
        "animation": style.animation,
        "keywords": style.keywords,
        "font_px": font_px(style, height),
        "frame": {"width": width, "height": height},
    }


# ----------------------------------------------------------------------------------------------- ASS
def ass_color(hex_color: str, opacity: float = 1.0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS alpha 00 is opaque, FF is transparent)."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    a = round((1 - opacity) * 255)
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def ass_time(ms: int) -> str:
    ms = max(0, ms)
    cs = round(ms / 10)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


_ASS_UNSAFE = re.compile(r"[{}\\]")


def _txt(word: str, style: CaptionStyle) -> str:
    t = _ASS_UNSAFE.sub("", word)  # braces and backslashes would be read as override tags
    return t.upper() if style.uppercase else t


def _is_keyword(word: str, style: CaptionStyle) -> bool:
    return bool(style.keywords) and re.sub(r"\W+", "", word.lower()) in {re.sub(r"\W+", "", k) for k in style.keywords}


def compile_ass(style: CaptionStyle, cues: list[Cue], width: int, height: int, *, title: str = "clip") -> str:
    ml, mr, mv = margins(style, width, height)
    align = {"bottom": 2, "middle": 5, "top": 8}[style.position]
    if (
        style.box.enabled
    ):  # an opaque box: its colour goes in both slots libass may read, the outline width is the padding
        border_style, outline = 3, round(px(style.box.padding_pct, height))
        outline_col = back_col = ass_color(style.box.color, style.box.opacity)
    else:
        border_style, outline = 1, round(px(style.outline_pct, height))
        outline_col = back_col = ass_color(style.outline_color)
    shadow = round(px(style.shadow_pct, height))
    karaoke = style.animation == "karaoke"
    # karaoke fills from the secondary colour to the primary one as each word is sung
    primary = ass_color(style.highlight_color if karaoke else style.color)
    secondary = ass_color(style.color if karaoke else style.highlight_color)
    head = (
        "[Script Info]\n"
        f"Title: {title}\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,{style.font_family},{ass_font_size(style, height)},{primary},{secondary},{outline_col},"
        f"{back_col},"
        f"{-1 if style.bold else 0},{-1 if style.italic else 0},0,0,100,100,0,0,{border_style},{outline},{shadow},"
        f"{align},{ml},{mr},{mv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    for cue in cues:
        events.extend(_cue_events(style, cue))
    return head + "\n".join(events) + ("\n" if events else "")


def _line_text(style: CaptionStyle, cue: Cue, active: int | None, karaoke: bool) -> str:
    """Text of a cue. `active` is the index of the word being spoken (highlight/pop modes), None for the rest."""
    out_lines: list[str] = []
    idx = 0
    for line in cue.lines:
        parts: list[str] = []
        for w in line:
            t = _txt(w.text, style)
            if karaoke:
                dur = max(1, round((w.end_ms - w.start_ms) / 10))
                tag = f"{{\\kf{dur}}}"
                parts.append(tag + t)
            elif active == idx and style.animation in ("highlight", "pop"):
                extra = "\\fscx118\\fscy118" if style.animation == "pop" else ""
                parts.append(f"{{\\c{ass_color(style.highlight_color)}{extra}}}{t}{{\\r}}")
            elif _is_keyword(w.text, style):
                parts.append(f"{{\\c{ass_color(style.keyword_color)}}}{t}{{\\r}}")
            else:
                parts.append(t)
            idx += 1
        out_lines.append(" ".join(parts))
    return "\\N".join(out_lines)


def _cue_events(style: CaptionStyle, cue: Cue) -> list[str]:
    fmt = "Dialogue: 0,{s},{e},Default,,0,0,0,,{t}"
    if style.animation == "karaoke":
        # karaoke timing is relative to the cue start; pad the first word if the cue starts before it does
        return [fmt.format(s=ass_time(cue.start_ms), e=ass_time(cue.end_ms), t=_line_text(style, cue, None, True))]
    if style.animation in ("highlight", "pop"):
        words = cue.words
        out = []
        for i, w in enumerate(words):
            start = w.start_ms if i else cue.start_ms
            end = words[i + 1].start_ms if i + 1 < len(words) else cue.end_ms
            end = max(end, start + 1)
            out.append(fmt.format(s=ass_time(start), e=ass_time(end), t=_line_text(style, cue, i, False)))
        return out
    return [fmt.format(s=ass_time(cue.start_ms), e=ass_time(cue.end_ms), t=_line_text(style, cue, None, False))]


def max_word_drift_ms(cues: list[Cue], words: list[tuple[str, int, int]], clip_start_ms: int) -> int:
    """How far a caption word's start is from the transcript's. Zero by construction; this catches a future bug."""
    src = {(t.strip(), s - clip_start_ms) for t, s, _ in words if t.strip()}
    worst = 0
    for w in (w for c in cues for w in c.words):
        near = min((abs(w.start_ms - s) for t, s in src if t == w.text), default=10**9)
        worst = max(worst, min(near, 10**9))
    return worst
