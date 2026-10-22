"""Find a streamer's webcam box once: grab a frame, draw a grid, optionally suggest a box.

The suggestion uses OpenCV face detection (`pip install -e '.[vision]'`) and is only a starting
point — confirm it on the PNG before writing it to config/streamers.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from clipfactory.config import NormBox
from clipfactory.textcard import load_font


@dataclass(frozen=True)
class Calibration:
    image: Path
    suggestion: NormBox | None


def box_around_face(
    fx: float,
    fy: float,
    fw: float,
    fh: float,
    frame_w: int,
    frame_h: int,
    *,
    scale: float = 3.6,
    aspect: float = 16 / 9,
    snap: float = 0.06,
) -> NormBox:
    """Typical webcam overlay around a detected face, snapped to nearby frame edges."""
    bw = fw * scale
    bh = bw / aspect
    if bh < fh * 2.2:
        bh = fh * 2.2
        bw = bh * aspect
    bw, bh = min(bw, frame_w), min(bh, frame_h)
    cx, cy = fx + fw / 2, fy + fh / 2 + fh * 0.25
    x0 = min(max(cx - bw / 2, 0.0), frame_w - bw)
    y0 = min(max(cy - bh / 2, 0.0), frame_h - bh)
    if x0 < snap * frame_w:
        x0 = 0.0
    elif frame_w - (x0 + bw) < snap * frame_w:
        x0 = frame_w - bw
    if y0 < snap * frame_h:
        y0 = 0.0
    elif frame_h - (y0 + bh) < snap * frame_h:
        y0 = frame_h - bh
    x, y = round(x0 / frame_w, 3), round(y0 / frame_h, 3)
    w = min(round(bw / frame_w, 3), round(1 - x, 3))
    h = min(round(bh / frame_h, 3), round(1 - y, 3))
    return NormBox(x=x, y=y, w=w, h=h)


def _face_cascade():
    """(cv2, cascade) or None when OpenCV with Haar cascades isn't installed."""
    try:
        import cv2
    except ImportError:
        return None
    cascade_cls = getattr(cv2, "CascadeClassifier", None)
    data = getattr(cv2, "data", None)
    if cascade_cls is None or data is None:
        return None  # OpenCV 5 dropped Haar cascades; the vision extra pins <5
    return cv2, cascade_cls(data.haarcascades + "haarcascade_frontalface_default.xml")


def face_detection_available() -> bool:
    return _face_cascade() is not None


def face_in_image(image: Image.Image) -> bool | None:
    """True if a face is visible; None if face detection isn't installed."""
    loaded = _face_cascade()
    if loaded is None:
        return None
    cv2, cascade = loaded
    img = image.convert("RGB")
    if img.width < 500:  # small webcam crops: upscale so smaller faces are found
        img = img.resize((img.width * 2, img.height * 2))
    gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    return len(faces) > 0


def suggest_webcam_box(image: Image.Image) -> NormBox | None:
    loaded = _face_cascade()
    if loaded is None:
        return None
    cv2, cascade = loaded
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    w, h = image.size
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, w // 40), max(24, h // 40))
    )
    if len(faces) == 0:
        return None
    fx, fy, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    return box_around_face(float(fx), float(fy), float(fw), float(fh), w, h)


def annotate(
    image: Image.Image,
    font_path: str,
    *,
    box: NormBox | None = None,
    suggestion: NormBox | None = None,
) -> Image.Image:
    img = image.convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    font = load_font(font_path, max(18, h // 40))
    for i in range(1, 10):
        x, y = w * i / 10, h * i / 10
        draw.line([(x, 0), (x, h)], fill=(255, 255, 255, 110), width=2)
        draw.line([(0, y), (w, y)], fill=(255, 255, 255, 110), width=2)
        label = f"{i / 10:.1f}"
        draw.text(
            (x + 4, 4),
            label,
            font=font,
            fill=(255, 255, 255, 230),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 230),
        )
        draw.text(
            (4, y + 4),
            label,
            font=font,
            fill=(255, 255, 255, 230),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 230),
        )
    for norm, color, name in (
        (suggestion, (0, 255, 120), "suggested"),
        (box, (255, 230, 0), "config"),
    ):
        if norm is None:
            continue
        rect = (norm.x * w, norm.y * h, (norm.x + norm.w) * w, (norm.y + norm.h) * h)
        draw.rectangle(rect, outline=(*color, 255), width=6)
        draw.text(
            (rect[0] + 10, rect[1] + 10),
            f"{name}: {norm.as_text()}",
            font=font,
            fill=(*color, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 255),
        )
    return img


def calibrate_frame(
    frame_png: Path,
    out_png: Path,
    font_path: str,
    *,
    box: NormBox | None = None,
    detect: bool = True,
) -> Calibration:
    image = Image.open(frame_png)
    suggestion = suggest_webcam_box(image) if detect else None
    annotate(image, font_path, box=box, suggestion=suggestion).save(out_png)
    return Calibration(out_png, suggestion)
