"""Experimental per-clip face-based webcam estimates, with conservative rejection.

Temporal agreement is not a probability of correctness or proof of panel borders.
Every accepted estimate still needs human review. No model download or paid service.
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from clipfactory.calibrate import FaceDetectionError, FaceDetector, box_around_face
from clipfactory.config import NormBox
from clipfactory.layout import webcam_box
from clipfactory.media import MediaError, extract_frame

log = logging.getLogger(__name__)

SAMPLE_POINTS = (0.0, 0.25, 0.50, 0.75, 1.0)
MIN_DETECTIONS = 4
MIN_IOU = 0.35


@dataclass(frozen=True)
class WebcamDetection:
    box: NormBox | None
    found: int
    checked: int
    reason: str

    @property
    def confident(self) -> bool:
        """Compatibility alias: passed heuristic gates, NOT calibrated confidence."""
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
    cx = box.x + box.w / 2
    cy = box.y + box.h / 2
    return cx <= 0.36 or cx >= 0.64 or cy <= 0.36 or cy >= 0.64


def _plausible_panel(box: NormBox) -> bool:
    # Reject large camera/scene estimates. These are safety limits, not learned priors.
    return _peripheral(box) and box.w <= 0.5 and box.h <= 0.6 and box.w * box.h <= 0.25


def _reposition_hint(found: NormBox, hint: NormBox) -> NormBox:
    x = min(max(found.x + (found.w - hint.w) / 2, 0.0), 1.0 - hint.w)
    y = min(max(found.y + (found.h - hint.h) / 2, 0.0), 1.0 - hint.h)
    # Only retain exact edge anchors; proximity alone is not evidence of a border.
    if found.x == 0:
        x = 0.0
    elif abs(1.0 - found.x - found.w) < 1e-6:
        x = 1.0 - hint.w
    if found.y == 0:
        y = 0.0
    elif abs(1.0 - found.y - found.h) < 1e-6:
        y = 1.0 - hint.h
    # Keep precision: rounding an edge coordinate up can put x+w outside the frame.
    return NormBox(x=x, y=y, w=hint.w, h=hint.h)


def crop_contains_faces(box: NormBox, faces: list[NormBox], frame_size: tuple[int, int]) -> bool:
    """Check the *actual* aspect-trimmed, even-pixel FFmpeg crop, not just the panel."""
    w, h = frame_size
    try:
        crop = webcam_box(w, h, box)
    except ValueError:
        return False
    return all(
        crop.x <= face.x * w
        and crop.y <= face.y * h
        and crop.x + crop.w >= (face.x + face.w) * w
        and crop.y + crop.h >= (face.y + face.h) * h
        for face in faces
    )


def choose_webcam_box(
    samples: list[NormBox | None],
    hint: NormBox | None = None,
    *,
    faces: list[NormBox] | None = None,
    frame_size: tuple[int, int] = (1920, 1080),
) -> WebcamDetection:
    """Require coverage and agreement; never average coordinates into an invalid box."""
    candidates = [b for b in samples if b is not None and _plausible_panel(b)]
    checked, found = len(samples), len(candidates)
    if found < max(MIN_DETECTIONS, math.ceil(checked * 0.8)):
        return WebcamDetection(None, found, checked, "too_few_faces")
    if samples[0] not in candidates or samples[-1] not in candidates:
        return WebcamDetection(None, found, checked, "missing_endpoint")
    # A rejected scene is not equivalent to one missed frontal face.
    if any(b is not None and not _plausible_panel(b) for b in samples):
        return WebcamDetection(None, found, checked, "implausible_scene")
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if intersection_over_union(a, b) < MIN_IOU:
                return WebcamDetection(None, found, checked, "inconsistent_positions")
            if max(a.w / b.w, b.w / a.w, a.h / b.h, b.h / a.h) > 1.8:
                return WebcamDetection(None, found, checked, "inconsistent_size")

    # Medoid is an observed valid box. Independent coordinate medians need not fit.
    estimate = max(candidates, key=lambda a: sum(intersection_over_union(a, b) for b in candidates))
    reason = "estimated_crop"
    if hint is not None:
        # Historical dimensions are a prior, never a border measurement.
        if (
            not _plausible_panel(hint)
            or max(
                hint.w / estimate.w,
                estimate.w / hint.w,
                hint.h / estimate.h,
                estimate.h / hint.h,
            )
            > 1.8
        ):
            return WebcamDetection(None, found, checked, "hint_size_mismatch")
        if intersection_over_union(estimate, hint) >= 0.40:
            estimate, reason = hint, "consistent_hint_estimate"
        else:
            estimate, reason = _reposition_hint(estimate, hint), "relocated_hint_estimate"
    if faces is not None and not crop_contains_faces(estimate, faces, frame_size):
        return WebcamDetection(None, found, checked, "face_outside_final_crop")
    return WebcamDetection(estimate, found, checked, reason)


def detect_webcam(
    source: Path,
    *,
    start: float,
    length: float,
    hint: NormBox | None,
    ffmpeg_bin: str,
    work_dir: Path,
    source_size: tuple[int, int] | None = None,
) -> WebcamDetection:
    """Inspect the selected render window, including both ends, at bounded resolution.

    Missing optional dependencies fail before decoding. An ambiguous or failed frame
    rejects the clip; a single interior no-face observation is tolerated. Sparse
    samples cannot rule out brief scene changes between observations.
    """
    if not math.isfinite(start) or not math.isfinite(length) or start < 0 or length < 1:
        return WebcamDetection(None, 0, 0, "invalid_window")
    try:
        detector = FaceDetector()
    except FaceDetectionError as exc:
        return WebcamDetection(None, 0, 0, str(exc))
    samples: list[NormBox | None] = []
    faces: list[NormBox] = []
    frame_size = source_size or (1920, 1080)
    for index, fraction in enumerate(SAMPLE_POINTS):
        try:
            frame = extract_frame(
                source,
                start + min(fraction * length, length - 0.1),
                work_dir / f"webcam_{index}.png",
                ffmpeg_bin,
                max_size=1280,
                timeout=15,
            )
            with Image.open(frame) as image:
                frame_size = source_size or image.size
                detected = detector.detect(image)
                # Do not silently discard a second face (game character, guest, PiP).
                if len(detected) > 1:
                    return WebcamDetection(None, len(faces), index + 1, "ambiguous_faces")
                if not detected:
                    samples.append(None)
                    continue
                face = detected[0]
                if not _peripheral(face):
                    return WebcamDetection(None, len(faces), index + 1, "central_face")
                w, h = image.size
                # Edge snapping is useful for manual calibration but unproven automatically.
                box = box_around_face(face.x * w, face.y * h, face.w * w, face.h * h, w, h, snap=0)
                samples.append(box)
                faces.append(face)
        except (
            MediaError,
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
            FaceDetectionError,
        ) as exc:
            log.debug("webcam frame %s unavailable (%s)", index, type(exc).__name__)
            return WebcamDetection(None, len(faces), index + 1, "frame_or_detector_failed")
    return choose_webcam_box(samples, hint, faces=faces, frame_size=frame_size)
