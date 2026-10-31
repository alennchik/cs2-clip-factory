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


class FaceDetectionError(RuntimeError):
    """Optional detector is unavailable or could not inspect a frame."""


class FaceDetector:
    """Load the optional cascade once per clip, returning *all* raw face boxes.

    A face box is not a webcam boundary. Calibration may expand it as a manual
    suggestion; automatic selection must also check ambiguity and the final crop.
    """

    def __init__(self) -> None:
        try:
            import cv2
        except (ImportError, OSError) as exc:
            raise FaceDetectionError("opencv_unavailable") from exc
        self.cv2 = cv2
        if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
            raise FaceDetectionError("cascade_unavailable")
        try:
            self.cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            if self.cascade.empty():
                raise FaceDetectionError("cascade_unavailable")
        except cv2.error as exc:
            raise FaceDetectionError("cascade_unavailable") from exc

    def detect(self, image: Image.Image) -> list[NormBox]:
        cv2 = self.cv2
        w, h = image.size
        try:
            gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
            faces = self.cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=6,
                minSize=(max(24, w // 40), max(24, h // 40)),
            )
        except cv2.error as exc:
            raise FaceDetectionError("detector_failed") from exc
        return [
            NormBox(x=float(x) / w, y=float(y) / h, w=float(fw) / w, h=float(fh) / h)
            for x, y, fw, fh in faces
        ]


def suggest_webcam_box(image: Image.Image) -> NormBox | None:
    try:
        faces = FaceDetector().detect(image)
    except FaceDetectionError:
        return None
    if not faces:
        return None
    face = max(faces, key=lambda f: f.w * f.h)
    w, h = image.size
    return box_around_face(face.x * w, face.y * h, face.w * w, face.h * h, w, h)


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
