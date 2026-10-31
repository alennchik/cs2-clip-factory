"""Real FFmpeg, synthetic pixels; face detections are explicitly controlled."""

import json
from dataclasses import replace

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image

from clipfactory import db
from clipfactory.cli import cli
from clipfactory.config import AppConfig, NormBox, RenderConfig, SponsorConfig, StreamerConfig
from clipfactory.media import extract_frame, probe
from clipfactory.pipeline import render_clip
from clipfactory.render import RenderResult, render_video
from clipfactory.webcam_auto import WebcamDetection

from .conftest import FONT, clip_record, make_clip, needs_ffmpeg


@needs_ffmpeg
def test_auto_fallback_ignores_zoom_and_preserves_hook_and_sponsor(clip_12s, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "clipfactory.webcam_auto.detect_webcam",
        lambda *a, **kw: WebcamDetection(None, 0, 5, "too_few_faces"),
    )
    banner = make_clip(tmp_path / "banner.mp4", duration=1, width=320, height=180)
    cfg = RenderConfig(
        font=str(FONT),
        auto_webcam=True,
        fit_zoom=2,
        sponsor=SponsorConfig(enabled=True, asset=banner, min_clip_seconds=2),
    )
    result = render_video(
        clip_12s,
        tmp_path / "fallback.mp4",
        cfg=cfg,
        layout="split",
        webcam=NormBox(x=0, y=0, w=0.24, h=0.235),
        hook_text="Тест",
        end=3,
    )
    assert result.layout == "full" and result.sponsor_duration > 0
    assert abs(probe(result.path).duration - 4) < 0.3
    frame = np.asarray(Image.open(extract_frame(result.path, 0.2, tmp_path / "result.png")))
    # Orange camera at the extreme source right survives the full fallback, even at cfg zoom=2.
    r, g, b = frame[1180, 1010, :3]
    assert r > 200 and 100 < g < 190 and b < 70
    yellow = (frame[:650, :, 0] > 210) & (frame[:650, :, 1] > 190) & (frame[:650, :, 2] < 90)
    assert yellow.sum() > 200  # hook is still above the fitted video


@needs_ffmpeg
@pytest.mark.parametrize("width,height", [(1920, 1080), (400, 1600)])
def test_detector_frame_extraction_is_bounded_and_keeps_aspect(tmp_path, width, height):
    source = make_clip(tmp_path / "src.mp4", duration=1, width=width, height=height, audio=False)
    out = extract_frame(source, 0.2, tmp_path / "frame.png", max_size=1280, timeout=15)
    with Image.open(out) as image:
        assert max(image.size) <= 1280
        assert abs(image.width / image.height - width / height) < 0.01


@needs_ffmpeg
@pytest.mark.parametrize("auto,layout", [(False, "split"), (True, "full")])
def test_manual_and_explicit_full_never_call_detector(
    clip_12s, tmp_path, monkeypatch, auto, layout
):
    monkeypatch.setattr(
        "clipfactory.webcam_auto.detect_webcam",
        lambda *a, **kw: pytest.fail("detection should be bypassed"),
    )
    result = render_video(
        clip_12s,
        tmp_path / "out.mp4",
        cfg=RenderConfig(font=str(FONT), auto_webcam=auto),
        layout=layout,
        webcam=NormBox(x=0.72, y=0.7, w=0.28, h=0.3),
        end=1,
    )
    assert result.layout == layout and result.webcam_detection is None


@pytest.mark.parametrize(
    "webcam,layout,auto,expected",
    [
        (None, "auto", True, "split"),
        (None, "full", True, "full"),
        (None, "auto", False, "full"),
        (NormBox(x=0, y=0, w=0.2, h=0.2), "auto", False, "split"),
    ],
)
def test_pipeline_routes_streamer_layout_and_persists_diagnostic(
    conn,
    settings,
    monkeypatch,
    webcam,
    layout,
    auto,
    expected,
):
    db.upsert_clip(conn, clip_record())
    src = settings.raw_dir / "source.mp4"
    src.write_bytes(b"fixture")
    db.save_media(
        conn,
        "Clip1",
        path=str(src),
        sha256="digest",
        bytes=7,
        width=640,
        height=360,
        fps=30,
        duration=5,
        has_audio=0,
        method="test",
        permission_ref="",
    )
    cfg = AppConfig(
        render=RenderConfig(auto_webcam=auto),
        streamers=[StreamerConfig(login="streamer_a", webcam=webcam, layout=layout)],
    )

    def fake_render(source, out, **kw):
        assert source == src and kw["layout"] == expected
        assert kw["webcam"] == webcam and kw["cfg"].auto_webcam == auto
        result = RenderResult(out, 0, 5, "full")
        if auto and expected == "split":
            result = replace(result, webcam_detection=WebcamDetection(None, 0, 5, "too_few_faces"))
        return result

    monkeypatch.setattr("clipfactory.pipeline.render_video", fake_render)
    rid = render_clip(conn, settings, cfg, db.get_clip(conn, "Clip1"), hook_options=[])
    event = db.latest_event_detail(conn, "webcam_detection", rid)
    if auto and expected == "split":
        assert json.loads(event)["reason"] == "too_few_faces"
    else:
        assert event is None


@pytest.mark.parametrize("args,enabled", [([], True), (["--no-auto-webcam"], False)])
def test_render_file_respects_config_and_explicit_disable(tmp_path, monkeypatch, args, enabled):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    config = tmp_path / "config.yaml"
    config.write_text("render:\n  auto_webcam: true\n")
    monkeypatch.setenv("CLIPFACTORY_CONFIG", str(config))

    def fake_render(src, out, **kwargs):
        assert kwargs["cfg"].auto_webcam == enabled
        assert kwargs["layout"] == ("split" if enabled else "full")
        return RenderResult(out, 0, 1, "full")

    monkeypatch.setattr("clipfactory.render.render_video", fake_render)
    result = CliRunner().invoke(cli, ["render-file", str(source), *args])
    assert result.exit_code == 0, result.output
