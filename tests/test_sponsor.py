"""Unit and FFmpeg integration tests for optional sponsor insertion."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipfactory.config import RenderConfig
from clipfactory.media import probe
from clipfactory.sponsor import SponsorError, choose_insertion_time, insert_sponsor

from .conftest import needs_ffmpeg


def test_sponsor_is_disabled_by_default():
    assert RenderConfig().sponsor.enabled is False


def test_chooses_quiet_pause_inside_middle():
    levels = np.full(120, -8.0, dtype=np.float32)
    levels[47:54] = -40.0
    at = choose_insertion_time(12.0, levels)
    assert 4.7 <= at <= 5.4


def test_absent_audio_envelope_uses_center():
    assert choose_insertion_time(20.0, np.array([], dtype=np.float32)) == 9.0


def test_invalid_window_rejected():
    with pytest.raises(SponsorError, match="invalid placement"):
        choose_insertion_time(20.0, np.array([]), start_fraction=0.8, end_fraction=0.2)


def test_missing_asset_is_rejected(tmp_path: Path):
    with pytest.raises(SponsorError, match="not found"):
        insert_sponsor(tmp_path / "clip.mp4", tmp_path / "missing.webm")


@needs_ffmpeg
def test_inserts_webm_and_preserves_clip_length(tmp_path: Path):
    source = tmp_path / "original.mp4"
    banner = tmp_path / "first_banner.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1080x1920:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=yellow:s=360x180:r=30:d=1.2",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "400k",
            str(banner),
        ],
        check=True,
        capture_output=True,
    )
    result = insert_sponsor(source, banner, min_clip_seconds=2.0, crf=30, max_bitrate="2M")
    assert result is not None
    assert 1.0 < result.at < 2.0
    assert abs(result.duration - 1.2) < 0.1
    output = probe(source)
    assert (output.width, output.height) == (1080, 1920)
    assert (output.vcodec, output.acodec) == ("h264", "aac")
    assert abs(output.duration - 4.2) < 0.3
