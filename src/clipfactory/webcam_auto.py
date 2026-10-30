"""Conservative per-clip webcam localization using the existing OpenCV face detector.

No video models or paid services. A stable face must appear in at least 3 of 5
sampled frames; inconsistent locations (including mid-clip moves) are rejected.
This is an estimate of the *webcam region*, not a guaranteed panel boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from clipfactory.calibrate import suggest_webcam_box
from clipfactory.config import NormBox
from clipfactory.media import MediaError, extract_frame

log = logging.getLogger(__name__)

SAMPLE_POINTS = (0.10, 0.30, 0.50, 0.70, 0.90)
MIN_DETECTIONS = 3
MIN_IOU = 0.35


@dataclass(frozen=True)
class WebcamDetection:
    box: NormBox | None
    found: int
    checked: int
    reason: str

    @property
    def confident(self) -> bool:
        return self.box is not None


def intersection_over_union(a: NormBox, b: NormBox) -> float:
    left = max(a.x, b.x)
    top = max(a.y, b.y)
    right = min(a.x + a.w, b.x + b.w)
    bottom = min(a.y + a.h, b.y + b.h)
    overlap = max(right - left, 0.0) * max(bottom - top, 0.0)
    union = a.w * a.h + b.w * b.h - overlap
    return overlap / union if union else 0.0


def _peripheral(box: NormBox) -> bool:
    """Reject likely in-game faces near the middle of the screen."""
    cx = box.x + box.w / 2
    cy = box.y + box.h / 2
    return cx <= 0.36 or cx >= 0.64 or cy <= 0.36 or cy >= 0.64


def _reposition_hint(found: NormBox, hint: NormBox) -> NormBox:
    """Keep previously approved webcam dimensions while relocating it.

    This avoids changing the visible face size when only the webcam location moved.
    Panel borders cannot be reliably inferred from a face alone.
    """
    x = min(max(found.x + (found.w - hint.w) / 2, 0.0), 1.0 - hint.w)
    y = min(max(found.y + (found.h - hint.h) / 2, 0.0), 1.0 - hint.h)
    # Face-based suggestions already snap near the frame edge; retain that anchor.
    if found.x <= 0.015:
        x = 0.0
    elif 1.0 - found.x - found.w <= 0.015:
        x = 1.0 - hint.w
    if found.y <= 0.015:
        y = 0.0
    elif 1.0 - found.y - found.h <= 0.015:
        y = 1.0 - hint.h
    return NormBox(x=round(x, 3), y=round(y, 3), w=hint.w, h=hint.h)


def choose_webcam_box(
    samples: list[NormBox | None], hint: NormBox | None = None
) -> WebcamDetection:
    """Require spatial agreement across time instead of trusting one face detection."""
    candidates = [b for b in samples if b is not None and _peripheral(b)]
    checked = len(samples)
    found = len(candidates)
    if found < MIN_DETECTIONS:
        return WebcamDetection(None, found, checked, "too_few_faces")

    # Reject changed webcam locations, false matches, and uncertain detections.
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if intersection_over_union(a, b) < MIN_IOU:
                return WebcamDetection(None, found, checked, "inconsistent_positions")

    # The medians are robust to face movement within a stationary camera panel.
    def median(values: list[float]) -> float:
        ordered = sorted(values)
        return ordered[len(ordered) // 2]

    estimate = NormBox(
        x=median([b.x for b in candidates]),
        y=median([b.y for b in candidates]),
        w=median([b.w for b in candidates]),
        h=median([b.h for b in candidates]),
    )
    if hint is not None:
        # Prefer a known-good crop when it overlaps the detected panel.
        if intersection_over_union(estimate, hint) >= 0.40:
            return WebcamDetection(hint, found, checked, "verified_existing_crop")
        return WebcamDetection(
            _reposition_hint(estimate, hint), found, checked, "relocated_existing_crop"
        )
    return WebcamDetection(estimate, found, checked, "estimated_crop")


def detect_webcam(
    source: Path,
    *,
    start: float,
    length: float,
    hint: NormBox | None,
    ffmpeg_bin: str,
    work_dir: Path,
) -> WebcamDetection:
    """Inspect five frames of the *selected render window* and vote on webcam location.

    An absent OpenCV extra produces no face detections, thus a safe full-layout
    fallback. Individual extraction failures also count as missing observations.
    """
    if length < 1:
        return WebcamDetection(None, 0, 0, "too_short")
    work_dir.mkdir(parents=True, exist_ok=True)
    samples: list[NormBox | None] = []
    for index, fraction in enumerate(SAMPLE_POINTS):
        try:
            frame = extract_frame(
                source, start + fraction * length, work_dir / f"webcam_{index}.png", ffmpeg_bin
            )
            with Image.open(frame) as image:
                image = image.convert("RGB")
                if image.width > 1280:
                    new_h = round(image.height * 1280 / image.width)
                    image = image.resize((1280, new_h), Image.Resampling.BILINEAR)
                samples.append(suggest_webcam_box(image))
        except (MediaError, OSError, ValueError) as exc:
            log.debug("webcam frame %s unavailable: %s", index, exc)
            samples.append(None)
    return choose_webcam_box(samples, hint)
