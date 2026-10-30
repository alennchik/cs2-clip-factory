"""Tests for conservative automatic webcam selection (OpenCV is optional)."""

from PIL import Image

from clipfactory.config import NormBox, RenderConfig
from clipfactory.render import render_video
from clipfactory.webcam_auto import WebcamDetection, choose_webcam_box, detect_webcam

from .conftest import FONT, needs_ffmpeg

HINT = NormBox(x=0, y=0, w=0.24, h=0.235)
TOP_LEFT = NormBox(x=0, y=0, w=0.2, h=0.21)
TOP_RIGHT = NormBox(x=0.77, y=0.01, w=0.2, h=0.21)


def test_stable_detection_preserves_a_known_good_crop():
    result = choose_webcam_box([TOP_LEFT, TOP_LEFT, None, TOP_LEFT, TOP_LEFT], HINT)
    assert result.box == HINT
    assert result.found == 4
    assert result.reason == "verified_existing_crop"


def test_relocated_webcam_preserves_dimensions_but_moves_to_new_corner():
    result = choose_webcam_box([TOP_RIGHT] * 5, HINT)
    assert result.confident
    assert result.reason == "relocated_existing_crop"
    assert result.box.w == HINT.w
    assert result.box.h == HINT.h
    assert 0.70 < result.box.x <= 0.76
    assert result.box.y < 0.04


def test_face_missing_falls_back_to_full_instead_of_blind_old_crop():
    result = choose_webcam_box([None, None, TOP_LEFT, None, TOP_LEFT], HINT)
    assert result.box is None
    assert result.reason == "too_few_faces"


def test_midclip_camera_move_is_rejected():
    result = choose_webcam_box([TOP_LEFT, TOP_LEFT, TOP_RIGHT, TOP_RIGHT, TOP_RIGHT], HINT)
    assert result.box is None
    assert result.reason == "inconsistent_positions"


def test_lone_center_face_not_mistaken_for_webcam():
    center = NormBox(x=0.41, y=0.42, w=0.18, h=0.18)
    result = choose_webcam_box([center] * 5, HINT)
    assert result.box is None


def test_frame_detection_samples_across_selected_window(tmp_path, monkeypatch):
    image_path = tmp_path / "frame.png"
    Image.new("RGB", (128, 72), "grey").save(image_path)
    times = []

    def fake_extract(path, at, out, ffmpeg_bin):
        times.append(at)
        return image_path

    monkeypatch.setattr("clipfactory.webcam_auto.extract_frame", fake_extract)
    monkeypatch.setattr("clipfactory.webcam_auto.suggest_webcam_box", lambda image: TOP_RIGHT)
    result = detect_webcam(
        tmp_path / "source.mp4", start=5, length=20, hint=HINT,
        ffmpeg_bin="ffmpeg", work_dir=tmp_path / "frames",
    )  # fmt: skip
    assert result.confident
    assert len(times) == 5
    assert times == [7.0, 11.0, 15.0, 19.0, 23.0]


@needs_ffmpeg
def test_render_switches_to_full_when_detection_is_uncertain(clip_12s, tmp_path, monkeypatch):
    def no_face(*args, **kwargs):
        return WebcamDetection(None, 0, 5, "too_few_faces")

    monkeypatch.setattr("clipfactory.webcam_auto.detect_webcam", no_face)
    cfg = RenderConfig(font=str(FONT), auto_webcam=True)
    output = render_video(
        clip_12s, tmp_path / "safe_full.mp4",
        cfg=cfg, layout="split", webcam=HINT, end=2.0,
    )  # fmt: skip
    assert output.layout == "full"


@needs_ffmpeg
def test_render_uses_relocated_webcam_crop(clip_12s, tmp_path, monkeypatch):
    moved = NormBox(x=0.76, y=0.0, w=0.24, h=0.235)

    def relocated(*args, **kwargs):
        return WebcamDetection(moved, 5, 5, "relocated_existing_crop")

    monkeypatch.setattr("clipfactory.webcam_auto.detect_webcam", relocated)
    cfg = RenderConfig(font=str(FONT), auto_webcam=True)
    output = render_video(
        clip_12s, tmp_path / "relocated.mp4",
        cfg=cfg, layout="split", webcam=HINT, end=2.0,
    )  # fmt: skip
    assert output.layout == "split"


@needs_ffmpeg
def test_render_auto_detects_webcam_without_an_existing_hint(clip_12s, tmp_path, monkeypatch):
    def detected(*args, **kwargs):
        return WebcamDetection(TOP_LEFT, 5, 5, "estimated_crop")

    monkeypatch.setattr("clipfactory.webcam_auto.detect_webcam", detected)
    cfg = RenderConfig(font=str(FONT), auto_webcam=True)
    result = render_video(
        clip_12s, tmp_path / "new_webcam.mp4",
        cfg=cfg, layout="split", webcam=None, end=2.0,
    )  # fmt: skip
    assert result.layout == "split"
