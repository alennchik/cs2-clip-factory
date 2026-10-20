"""On-video text as transparent PNG layers (Pillow), overlaid by FFmpeg.

Using Pillow instead of FFmpeg's drawtext/libass keeps rendering independent of how the local
ffmpeg was built.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from clipfactory.layout import HOOK_MAX_W, HOOK_TOP, OUT_H, OUT_W

RGB = tuple[int, int, int]


def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


@dataclass(frozen=True)
class TextStyle:
    fill: RGB = (255, 230, 0)
    stroke_fill: RGB = (0, 0, 0)
    max_size: int = 92
    min_size: int = 52
    max_lines: int = 3
    stroke_ratio: float = 0.09
    line_spacing: float = 1.05
    shadow: bool = True


HOOK_STYLE = TextStyle()
CREDIT_STYLE = TextStyle(
    fill=(255, 255, 255), max_size=38, min_size=30, max_lines=1, stroke_ratio=0.12, shadow=False
)
CAPTION_STYLE = TextStyle(fill=(255, 255, 255), max_size=70, min_size=50, max_lines=2)


@functools.lru_cache(maxsize=64)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def text_width(text: str, font: ImageFont.FreeTypeFont, stroke: int) -> int:
    left, _, right, _ = font.getbbox(text, stroke_width=stroke)
    return int(right - left)


def wrap_lines(text: str, font: ImageFont.FreeTypeFont, max_width: int, stroke: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or text_width(trial, font, stroke) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_text(
    text: str, font_path: str, max_width: int, style: TextStyle = HOOK_STYLE
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Biggest font size whose wrapped lines fit the width and line limit."""
    size = style.max_size
    while True:
        font = load_font(font_path, size)
        stroke = max(2, round(size * style.stroke_ratio))
        lines = wrap_lines(text, font, max_width, stroke)
        fits = len(lines) <= style.max_lines and all(
            text_width(line, font, stroke) <= max_width for line in lines
        )
        if fits or size <= style.min_size:
            break
        size = max(style.min_size, size - 4)
    if len(lines) > style.max_lines:
        lines = lines[: style.max_lines]
        lines[-1] = lines[-1].rstrip(" ,.;:") + "…"
    return font, lines, stroke


def render_text_layer(
    text: str,
    out_path: str | Path,
    *,
    font_path: str,
    top: int = HOOK_TOP,
    max_width: int = HOOK_MAX_W,
    style: TextStyle = HOOK_STYLE,
    align: str = "center",
    left: int = 40,
    canvas: tuple[int, int] = (OUT_W, OUT_H),
) -> Path:
    """Full-frame transparent PNG with the text drawn at `top` (centered or left-aligned)."""
    font, lines, stroke = fit_text(text, font_path, max_width, style)
    ascent, descent = font.getmetrics()
    line_h = round((ascent + descent) * style.line_spacing)
    anchor = "ma" if align == "center" else "la"
    x = canvas[0] // 2 if align == "center" else left

    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    if style.shadow:
        shadow = Image.new("RGBA", canvas, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        for i, line in enumerate(lines):
            sdraw.text(
                (x, top + i * line_h + 6),
                line,
                font=font,
                anchor=anchor,
                fill=(0, 0, 0, 170),
                stroke_width=stroke,
                stroke_fill=(0, 0, 0, 170),
            )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=6)))
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text(
            (x, top + i * line_h),
            line,
            font=font,
            anchor=anchor,
            fill=(*style.fill, 255),
            stroke_width=stroke,
            stroke_fill=(*style.stroke_fill, 255),
        )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path
