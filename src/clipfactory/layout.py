"""Pure geometry for the 9:16 layouts. No I/O, fully unit-tested.

split: streamer webcam (1080x770) and game/broadcast (1080x1150) stacked; the webcam is on
       top by default (`webcam_position`), the boundary between them is the "seam"
full:  blurred cover background + the whole frame fitted in the middle (no webcam)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from clipfactory.config import NormBox

OUT_W = 1080
OUT_H = 1920
GAME_H = 1150
CAM_H = OUT_H - GAME_H  # 770
TOP_H, BOTTOM_H = GAME_H, CAM_H  # names from the game-on-top layout, kept for compatibility

# Text placement (TikTok UI safe zones: tabs at the top, buttons on the right,
# caption at the bottom; tall phones also crop ~9% off each side).
HOOK_TOP = 300
HOOK_MAX_W = 840


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    def crop_filter(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


def even_down(value: float) -> int:
    n = int(math.floor(value))
    return n - (n % 2)


def cover_box(
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    center_x: float = 0.5,
    center_y: float = 0.5,
) -> Box:
    """Largest box with the target aspect ratio inside the source, centered where asked."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source size must be positive")
    target_ar = target_w / target_h
    if src_w / src_h > target_ar:
        h, w = float(src_h), src_h * target_ar
    else:
        w, h = float(src_w), src_w / target_ar
    w_i = min(max(even_down(w), 2), even_down(src_w))
    h_i = min(max(even_down(h), 2), even_down(src_h))
    x = min(max(center_x * src_w - w_i / 2, 0.0), float(src_w - w_i))
    y = min(max(center_y * src_h - h_i / 2, 0.0), float(src_h - h_i))
    return Box(even_down(x), even_down(y), w_i, h_i)


def norm_to_px(box: NormBox, src_w: int, src_h: int) -> Box:
    x = even_down(box.x * src_w)
    y = even_down(box.y * src_h)
    w = even_down(min(box.w * src_w, src_w - x))
    h = even_down(min(box.h * src_h, src_h - y))
    if w < 16 or h < 16:
        raise ValueError("webcam box is too small for this video")
    return Box(x, y, w, h)


def webcam_box(src_w: int, src_h: int, cam: NormBox) -> Box:
    """Crop of the webcam area trimmed to the webcam panel's aspect ratio."""
    px = norm_to_px(cam, src_w, src_h)
    inner = cover_box(px.w, px.h, OUT_W, CAM_H)
    return Box(px.x + inner.x, px.y + inner.y, inner.w, inner.h)


def game_box(src_w: int, src_h: int, center_x: float = 0.5) -> Box:
    return cover_box(src_w, src_h, OUT_W, GAME_H, center_x=center_x)


def fit_size(src_w: int, src_h: int, zoom: float = 1.0) -> tuple[int, int]:
    """Foreground size for the full layout: frame width (times zoom), keeps aspect ratio."""
    w = even_down(OUT_W * zoom)
    h = even_down(w * src_h / src_w)
    return w, h


def full_foreground_box(src_w: int, src_h: int, zoom: float = 1.0) -> Box:
    """Where the fitted foreground lands in the output frame (after cropping overflow)."""
    w, h = fit_size(src_w, src_h, zoom)
    w, h = min(w, OUT_W), min(h, OUT_H)
    return Box((OUT_W - w) // 2, even_down((OUT_H - h) / 2), w, h)


def seam_y(webcam_position: str = "top") -> int:
    """Y of the boundary between the webcam and game panels."""
    return CAM_H if webcam_position == "top" else GAME_H


def hook_center_y(
    layout: str, src_w: int, src_h: int, zoom: float = 1.0, webcam_position: str = "top"
) -> int:
    """Where a "middle" hook is centered: on the seam, or just above the fitted frame."""
    if layout == "split":
        return seam_y(webcam_position)
    fg = full_foreground_box(src_w, src_h, zoom)
    return max(fg.y - 110, HOOK_TOP + 60)


def credit_top(
    layout: str, src_w: int, src_h: int, zoom: float = 1.0, webcam_position: str = "top"
) -> int:
    if layout == "split":
        return seam_y(webcam_position) + 22
    fg = full_foreground_box(src_w, src_h, zoom)
    return fg.y + fg.h + 24


def captions_top(
    layout: str, src_w: int, src_h: int, zoom: float = 1.0, webcam_position: str = "top"
) -> int:
    if layout == "split":
        return seam_y(webcam_position) - 160
    fg = full_foreground_box(src_w, src_h, zoom)
    return fg.y + fg.h + 100


def choose_window(
    duration: float, max_len: float, focus: tuple[float, float] | None = None
) -> tuple[float, float]:
    """(start, length) of the part to keep.

    Whole clip if it fits. Otherwise keep the ending (Twitch clips usually build up to the
    moment), shifted earlier when needed so the focus span (the hook line) stays inside.
    """
    if duration <= max_len:
        return 0.0, round(duration, 3)
    start = duration - max_len
    if focus is not None:
        f0, f1 = focus
        if f0 < start:
            start = max(0.0, f0 - 2.0)
        if f1 > start + max_len:
            start = max(0.0, min(f1 - max_len + 0.5, duration - max_len))
    return round(start, 3), round(max_len, 3)
