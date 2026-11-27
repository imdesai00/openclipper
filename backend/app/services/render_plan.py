"""The ffmpeg command for a clip render, as data. Pure: builds argument lists and filter graphs, runs nothing.

Argument LISTS only, never a shell string. Every number that reaches a filter graph is computed here; the only text
that does is a file path this process made itself, escaped for filter syntax."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.services.crop_logic import ASPECTS, CropSpec, crop_exprs

FPS = 30
PREVIEW_LONG_EDGE = 640
TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11.0
AUDIO_ONLY_BG = "#1B1B2F"


def output_size(kind: str, aspect: str) -> tuple[int, int]:
    w, h = ASPECTS[aspect]
    if kind == "final":
        return w, h
    scale = PREVIEW_LONG_EDGE / max(w, h)
    return round(w * scale) // 2 * 2, round(h * scale) // 2 * 2


@dataclass(frozen=True)
class RenderSpec:
    kind: str  # preview | final
    aspect: str
    src_w: int | None
    src_h: int | None
    has_video: bool
    has_audio: bool
    start_ms: int
    end_ms: int
    crop: CropSpec
    background: dict[str, Any]
    gain_db: float
    mute: bool
    ass_path: str
    measured: dict[str, float] | None = None  # first-pass loudnorm numbers (final renders)

    @property
    def duration_s(self) -> float:
        return (self.end_ms - self.start_ms) / 1000

    @property
    def size(self) -> tuple[int, int]:
        return output_size(self.kind, self.aspect)

    @property
    def audio_silent_by_design(self) -> bool:
        return self.mute or not self.has_audio


def fesc(path: str) -> str:
    """Escape a value for use inside a quoted filter option (backslash, colon, quote, comma, brackets)."""
    return re.sub(r"([\\:',\[\]])", r"\\\1", path)


def _hex(color: str) -> str:
    c = color.lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        raise ValueError("colour must look like #RRGGBB")
    return "0x" + c.upper()


def _bg_color(bg: dict[str, Any], default: str, *, only_if_solid: bool) -> str:
    """The background colour. A source with no picture of its own uses the default unless a solid colour was chosen."""
    chosen = bg.get("color") if (bg.get("type") == "solid" or not only_if_solid) else None
    return _hex(str(chosen or default))


def _needs_color_input(spec: RenderSpec) -> bool:
    if not spec.has_video:
        return True
    return spec.crop.mode == "fit" and spec.background.get("type") == "solid"


def video_graph(spec: RenderSpec, color_idx: int | None, audio_split: bool) -> str:
    """Filter graph that ends in the label [v], captions burned in."""
    ow, oh = spec.size
    ass = f"ass=filename='{fesc(spec.ass_path)}'"
    if not spec.has_video:
        wave_h = max(2, oh // 4 // 2 * 2)
        return (
            # the drawing copy is levelled so a quiet recording shows a visible shape; the real audio is untouched
            f"[a_wave]dynaudnorm=f=100:g=5:p=0.95,showwaves=s={ow}x{wave_h}:mode=cline:scale=sqrt:draw=full:rate={FPS}"
            ":colors=white,format=rgba[wv];"
            f"[{color_idx}:v][wv]overlay=0:(H-h)/2:shortest=1,setsar=1,fps={FPS},{ass}[v]"
        )
    assert spec.src_w and spec.src_h
    if spec.crop.mode == "fit":
        if spec.background.get("type") == "solid":
            return (
                f"[0:v]scale={ow}:{oh}:force_original_aspect_ratio=decrease[fg];"
                f"[{color_idx}:v][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1,fps={FPS},{ass}[v]"
            )
        sigma = max(8, oh // 36)
        return (
            "[0:v]split=2[s1][s2];"
            f"[s1]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},gblur=sigma={sigma}[bg];"
            f"[s2]scale={ow}:{oh}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},{ass}[v]"
        )
    w, h, x, y = crop_exprs(spec.crop, spec.src_w, spec.src_h, spec.aspect)
    return f"[0:v]crop=w={w}:h={h}:x='{x}':y='{y}',scale={ow}:{oh}:flags=lanczos,setsar=1,fps={FPS},{ass}[v]"


def audio_chain(spec: RenderSpec, source_label: str) -> str:
    parts: list[str] = []
    if spec.mute:
        parts.append("volume=0")  # linear zero: real silence (0 dB would mean "unchanged")
    elif spec.gain_db:
        parts.append(f"volume={spec.gain_db:g}dB")
    m = spec.measured
    if spec.kind == "final" and m and not spec.mute and spec.has_audio and m.get("input_i", -99) > -70:
        parts.append(
            f"loudnorm=I={TARGET_LUFS:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}:measured_I={m['input_i']:.2f}"
            f":measured_TP={m['input_tp']:.2f}:measured_LRA={m['input_lra']:.2f}:measured_thresh={m['input_thresh']:.2f}"
            f":offset={m['target_offset']:.2f}:linear=true"
        )
    parts.append("aresample=48000")
    parts.append("aformat=channel_layouts=stereo")
    return f"{source_label}{','.join(parts)}[a]"


def build_parts(spec: RenderSpec) -> tuple[list[str], list[str]]:
    """(options before the main -i, everything after it except the output file). Split so the progress-reporting
    runner can put the input path between them."""
    dur = f"{spec.duration_s:.3f}"
    before: list[str] = ["-ss", f"{spec.start_ms / 1000:.3f}", "-t", dur]
    after: list[str] = []
    next_idx = 1
    color_idx: int | None = None
    if _needs_color_input(spec):
        ow, oh = spec.size
        color = _bg_color(
            spec.background, AUDIO_ONLY_BG if not spec.has_video else "#000000", only_if_solid=not spec.has_video
        )
        after += ["-f", "lavfi", "-t", dur, "-i", f"color=c={color}:s={ow}x{oh}:r={FPS}"]
        color_idx, next_idx = next_idx, next_idx + 1
    silent_idx: int | None = None
    if spec.has_video and not spec.has_audio:
        after += ["-f", "lavfi", "-t", dur, "-i", "anullsrc=r=48000:cl=stereo"]
        silent_idx, next_idx = next_idx, next_idx + 1

    graph = video_graph(spec, color_idx, not spec.has_video)
    if not spec.has_video:
        a_src = "[a_src]"
        graph = "[0:a]asplit=2[a_wave][a_src];" + graph
    elif silent_idx is not None:
        a_src = f"[{silent_idx}:a]"
    else:
        a_src = "[0:a]"
    graph += ";" + audio_chain(spec, a_src)

    final = spec.kind == "final"
    after += [
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium" if final else "ultrafast",
        "-crf",
        "20" if final else "32",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high" if final else "baseline",
        "-c:a",
        "aac",
        "-b:a",
        "192k" if final else "64k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-t",
        dur,
    ]
    return before, after


def build_args(spec: RenderSpec, input_path: str, output_path: str) -> list[str]:
    before, after = build_parts(spec)
    return [*before, "-i", input_path, *after, output_path]


# ----------------------------------------------------------------------------------------------- loudness
def measure_args(spec: RenderSpec, input_path: str) -> list[str]:
    """First loudnorm pass: decode the clip's audio exactly as the render will hear it and print the numbers."""
    vol = f"volume={spec.gain_db:g}dB," if spec.gain_db else ""
    return [
        "-ss",
        f"{spec.start_ms / 1000:.3f}",
        "-t",
        f"{spec.duration_s:.3f}",
        "-i",
        input_path,
        "-vn",
        "-af",
        f"{vol}loudnorm=I={TARGET_LUFS:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}:print_format=json",
        "-f",
        "null",
        "-",
    ]


_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def parse_loudnorm(stderr: str) -> dict[str, float]:
    m = _JSON.search(stderr)
    if not m:
        raise ValueError("loudnorm printed no measurement")
    raw = json.loads(m.group(0))
    out: dict[str, float] = {}
    for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        v = str(raw[k]).strip().lower()
        out[k] = -99.0 if v in ("-inf", "inf", "nan") else float(v)
    return out


_EBU = re.compile(r"I:\s+(-?[0-9.]+)\s+LUFS")


def parse_integrated_lufs(stderr: str) -> float | None:
    """Integrated loudness from the ebur128 filter's final summary (the last 'I:' line)."""
    found = _EBU.findall(stderr)
    return float(found[-1]) if found else None


_BLACK = re.compile(r"black_duration:([0-9.]+)")
_SILENCE = re.compile(r"silence_duration:\s*([0-9.]+)")


def sum_black(stderr: str) -> float:
    return sum(float(x) for x in _BLACK.findall(stderr))


def sum_silence(stderr: str) -> float:
    return sum(float(x) for x in _SILENCE.findall(stderr))
