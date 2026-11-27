"""Where the crop window is, at every moment of a clip. Pure.

A clip has a constant `zoom` and a list of pan keyframes (the window's centre at a time). ffmpeg's crop filter can move
the window per frame but cannot change its size per frame, so zoom is constant by design (D-061)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ASPECTS: dict[str, tuple[int, int]] = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
MAX_KEYFRAMES = 24
MAX_ZOOM = 4.0


class CropError(ValueError):
    pass


@dataclass(frozen=True)
class Keyframe:
    t_ms: int  # relative to the start of the clip
    cx: float  # window centre as a fraction of the source width and height
    cy: float


@dataclass(frozen=True)
class CropSpec:
    mode: Literal["fill", "fit"] = "fill"
    zoom: float = 1.0
    keyframes: tuple[Keyframe, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "zoom": self.zoom,
            "keyframes": [{"t_ms": k.t_ms, "cx": k.cx, "cy": k.cy} for k in self.keyframes],
        }


def _unit(v: Any, name: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError) as exc:
        raise CropError(f"{name} must be a number between 0 and 1") from exc
    if not 0.0 <= f <= 1.0:
        raise CropError(f"{name} must be between 0 and 1")
    return round(f, 4)


def normalize_crop(raw: dict[str, Any] | None, clip_duration_ms: int) -> CropSpec:
    raw = raw or {}
    mode = raw.get("mode", "fill")
    if mode not in ("fill", "fit"):
        raise CropError("mode must be fill or fit")
    try:
        zoom = float(raw.get("zoom", 1.0))
    except (TypeError, ValueError) as exc:
        raise CropError("zoom must be a number") from exc
    if not 1.0 <= zoom <= MAX_ZOOM:
        raise CropError(f"zoom must be between 1 and {MAX_ZOOM:g}")
    kfs_raw = raw.get("keyframes") or []
    if len(kfs_raw) > MAX_KEYFRAMES:
        raise CropError(f"at most {MAX_KEYFRAMES} keyframes")
    seen: dict[int, Keyframe] = {}
    for k in kfs_raw:
        try:
            t = int(k["t_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CropError("each keyframe needs t_ms") from exc
        if not 0 <= t <= clip_duration_ms:
            raise CropError("a keyframe is outside the clip")
        seen[t] = Keyframe(t, _unit(k.get("cx", 0.5), "cx"), _unit(k.get("cy", 0.5), "cy"))
    return CropSpec(mode, round(zoom, 3), tuple(seen[t] for t in sorted(seen)))


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def window_size(src_w: int, src_h: int, aspect: str, zoom: float) -> tuple[int, int]:
    """The largest window of the target aspect that fits in the source, divided by the zoom. Even numbers (yuv420p)."""
    aw, ah = ASPECTS[aspect]
    ratio = aw / ah
    w = min(float(src_w), src_h * ratio)
    h = w / ratio
    return _even(w / zoom), _even(h / zoom)


def pan_at(spec: CropSpec, t_ms: float) -> tuple[float, float]:
    k = spec.keyframes
    if not k:
        return 0.5, 0.5
    if t_ms <= k[0].t_ms:
        return k[0].cx, k[0].cy
    for a, b in zip(k, k[1:], strict=False):
        if t_ms <= b.t_ms:
            f = (t_ms - a.t_ms) / (b.t_ms - a.t_ms)
            return a.cx + (b.cx - a.cx) * f, a.cy + (b.cy - a.cy) * f
    return k[-1].cx, k[-1].cy


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def window_at(spec: CropSpec, src_w: int, src_h: int, aspect: str, t_ms: float) -> tuple[int, int, int, int]:
    """(x, y, w, h) in source pixels at time t. The window never leaves the picture."""
    w, h = window_size(src_w, src_h, aspect, spec.zoom)
    cx, cy = pan_at(spec, t_ms)
    x = _clamp(cx * src_w - w / 2, 0, src_w - w)
    y = _clamp(cy * src_h - h / 2, 0, src_h - h)
    return int(round(x)), int(round(y)), w, h


def _piecewise(points: list[tuple[float, float]]) -> str:
    """ffmpeg expression in `t` (seconds): linear between points, flat outside them."""
    if len(points) == 1 or all(abs(p[1] - points[0][1]) < 1e-9 for p in points):
        return f"{points[0][1]:.3f}"
    expr = f"{points[-1][1]:.3f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:], strict=False))):
        seg = f"{v0:.3f}+({v1 - v0:.4f})*(t-{t0:.3f})/{max(t1 - t0, 1e-3):.3f}"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return f"if(lt(t,{points[0][0]:.3f}),{points[0][1]:.3f},{expr})"


def crop_exprs(spec: CropSpec, src_w: int, src_h: int, aspect: str) -> tuple[int, int, str, str]:
    """(w, h, x_expr, y_expr) for ffmpeg's crop filter. Keyframe positions are clamped first, so the straight line
    between two valid positions is valid too."""
    w, h = window_size(src_w, src_h, aspect, spec.zoom)
    if not spec.keyframes:
        x, y, _, _ = window_at(spec, src_w, src_h, aspect, 0)
        return w, h, str(x), str(y)
    xs, ys = [], []
    for k in spec.keyframes:
        x, y, _, _ = window_at(spec, src_w, src_h, aspect, k.t_ms)
        xs.append((k.t_ms / 1000, float(x)))
        ys.append((k.t_ms / 1000, float(y)))
    return w, h, _piecewise(xs), _piecewise(ys)
