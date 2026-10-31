"""Regression cases from the audit; these do not measure real-world face accuracy."""

import subprocess

import pytest

from clipfactory.config import NormBox
from clipfactory.webcam_auto import choose_webcam_box, detect_webcam

PANEL = NormBox(x=0, y=0, w=0.24, h=0.235)


@pytest.mark.parametrize("samples", [[PANEL] * 3 + [None, None], [None, None] + [PANEL] * 3])
def test_disappearance_at_either_end_is_not_a_safe_static_crop(samples):
    assert choose_webcam_box(samples).box is None


def test_consensus_does_not_construct_an_impossible_box_from_independent_medians():
    boxes = [NormBox(x=x, y=0, w=1 - x, h=0.2) for x in [0.7, 0.75, 0.8, 0.8]]
    result = choose_webcam_box(boxes)
    assert result.box is None or result.box.x + result.box.w <= 1


def test_relocating_high_precision_hint_stays_inside_frame():
    hint = NormBox(x=0, y=0, w=0.2404, h=0.235)
    moved = NormBox(x=0.8, y=0, w=0.2, h=0.2)
    result = choose_webcam_box([moved] * 5, hint)
    assert result.box is not None
    assert result.box.x + result.box.w <= 1


def test_fullscreen_sized_face_estimate_is_rejected():
    fullscreen = NormBox(x=0, y=0, w=0.7, h=0.7)
    assert choose_webcam_box([fullscreen] * 5).box is None


def test_frame_extraction_timeout_is_a_fallback(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 15)

    monkeypatch.setattr("clipfactory.webcam_auto.extract_frame", timeout)
    result = detect_webcam(
        tmp_path / "clip.mp4", start=0, length=5, hint=PANEL,
        ffmpeg_bin="ffmpeg", work_dir=tmp_path / "frames",
    )  # fmt: skip
    assert result.box is None


@pytest.mark.parametrize("x,y", [(0, 0), (0.76, 0), (0, 0.765), (0.76, 0.765)])
def test_all_corners_with_head_motion_and_final_crop_containment(x, y):
    panel = NormBox(x=x, y=y, w=0.24, h=0.235)
    faces = [
        NormBox(x=x + 0.10 + dx, y=y + 0.06, w=0.04, h=0.08)
        for dx in [-0.015, 0, 0.01, 0.005, -0.01]
    ]
    result = choose_webcam_box([panel] * 5, PANEL, faces=faces)
    assert result.box is not None
    assert result.box.x == pytest.approx(x)
    assert result.box.y == pytest.approx(y)


def test_overlap_is_not_proof_that_old_hint_contains_face():
    detected = NormBox(x=0.10, y=0, w=0.24, h=0.235)
    face = NormBox(x=0.25, y=0.05, w=0.05, h=0.08)
    result = choose_webcam_box([detected] * 5, PANEL, faces=[face] * 5)
    assert result.reason == "face_outside_final_crop"
    assert result.box is None


def test_face_in_panel_but_outside_aspect_trimmed_crop_is_rejected():
    panel = NormBox(x=0, y=0, w=0.4, h=0.2)
    face = NormBox(x=0.02, y=0.05, w=0.05, h=0.08)
    assert choose_webcam_box([panel] * 5, faces=[face] * 5).box is None


@pytest.mark.parametrize(
    "hint",
    [
        NormBox(x=0, y=0, w=0.05, h=0.05),
        NormBox(x=0, y=0, w=0.24, h=0.55),
        NormBox(x=0, y=0, w=0.5, h=0.235),
    ],
)
def test_incompatible_historical_size_or_aspect_ratio_falls_back(hint):
    result = choose_webcam_box([PANEL] * 5, hint)
    assert result.box is None and result.reason == "hint_size_mismatch"


def _run_detector(tmp_path, monkeypatch, detections):
    from PIL import Image

    frame = tmp_path / "frame.png"
    Image.new("RGB", (1280, 720), "black").save(frame)
    monkeypatch.setattr("clipfactory.webcam_auto.extract_frame", lambda *a, **kw: frame)

    class Detector:
        def detect(self, image):
            return detections

    monkeypatch.setattr("clipfactory.webcam_auto.FaceDetector", Detector)
    return detect_webcam(
        tmp_path / "src.mp4",
        start=0,
        length=5,
        hint=PANEL,
        ffmpeg_bin="ffmpeg",
        work_dir=tmp_path / "frames",
    )


def test_multiple_faces_do_not_select_largest(tmp_path, monkeypatch):
    faces = [NormBox(x=0.08, y=0.05, w=0.04, h=0.08), NormBox(x=0.42, y=0.35, w=0.14, h=0.25)]
    result = _run_detector(tmp_path, monkeypatch, faces)
    assert result.box is None and result.reason == "ambiguous_faces"


def test_no_face_black_frame_falls_back(tmp_path, monkeypatch):
    result = _run_detector(tmp_path, monkeypatch, [])
    assert result.box is None


def test_missing_opencv_does_not_decode_any_frames(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.setattr(
        "clipfactory.webcam_auto.extract_frame",
        lambda *a, **kw: pytest.fail("must not decode without detector"),
    )
    result = detect_webcam(
        tmp_path / "src.mp4",
        start=0,
        length=5,
        hint=PANEL,
        ffmpeg_bin="ffmpeg",
        work_dir=tmp_path / "frames",
    )
    assert result.box is None and result.reason == "opencv_unavailable"
    assert result.checked == 0


def test_opencv_runtime_error_is_a_fallback(tmp_path, monkeypatch):
    from PIL import Image

    from clipfactory.calibrate import FaceDetectionError

    frame = tmp_path / "frame.png"
    Image.new("RGB", (128, 72)).save(frame)
    monkeypatch.setattr("clipfactory.webcam_auto.extract_frame", lambda *a, **kw: frame)

    class Detector:
        def detect(self, image):
            raise FaceDetectionError("detector_failed")

    monkeypatch.setattr("clipfactory.webcam_auto.FaceDetector", Detector)
    result = detect_webcam(
        tmp_path / "src.mp4",
        start=0,
        length=5,
        hint=PANEL,
        ffmpeg_bin="ffmpeg",
        work_dir=tmp_path / "frames",
    )
    assert result.box is None and result.checked == 1


def test_actual_opencv_black_image_and_failed_cascade(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from PIL import Image

    from clipfactory.calibrate import FaceDetectionError, FaceDetector

    assert FaceDetector().detect(Image.new("RGB", (640, 360))) == []
    monkeypatch.setattr(cv2.data, "haarcascades", "/nonexistent/")
    with pytest.raises(FaceDetectionError, match="cascade_unavailable"):
        FaceDetector()
