from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clipfactory import db
from clipfactory.settings import Settings

REPO = Path(__file__).resolve().parents[1]
FONT = REPO / "assets" / "fonts" / "InterDisplay-Black.otf"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")

# Synthetic "stream": dark game feed with an orange webcam box in the bottom-right corner,
# a 440 Hz tone that gets 5x louder between 6 s and 7 s (the "moment").
WEBCAM = (0.72, 0.70, 0.28, 0.30)


def make_clip(
    path: Path,
    *,
    duration: float = 12.0,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    audio: bool = True,
) -> Path:
    x, y, w, h = WEBCAM
    video = (
        f"color=c=0x1d3557:size={width}x{height}:rate={fps}:duration={duration},"
        f"drawbox=x=iw*{x}:y=ih*{y}:w=iw*{w}:h=ih*{h}:color=0xff8c00:t=fill,"
        "drawbox=x=iw*0.45:y=ih*0.45:w=iw*0.1:h=ih*0.1:color=white:t=fill"
    )
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video]
    if audio:
        tone = f"aevalsrc='0.1*sin(2*PI*440*t)*(1+4*between(t,6,7))':s=48000:d={duration}"
        cmd += ["-f", "lavfi", "-i", tone, "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture(scope="session")
def clip_12s(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    return make_clip(tmp_path_factory.mktemp("media") / "clip_12s.mp4")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", out_dir=tmp_path / "out")
    s.ensure_dirs()
    return s


@pytest.fixture
def conn(settings: Settings):
    connection = db.connect(settings.db_path)
    yield connection
    connection.close()


def clip_record(clip_id: str = "Clip1", login: str = "streamer_a", **overrides) -> dict:
    record = {
        "clip_id": clip_id,
        "broadcaster_id": "1001",
        "broadcaster_login": login,
        "broadcaster_name": login.title(),
        "title": "ЭЛЕКТРОНИК ЗАМАШНИЛ",
        "url": f"https://clips.twitch.tv/{clip_id}",
        "thumbnail_url": "",
        "game_id": "32399",
        "language": "ru",
        "video_id": "v1",
        "vod_offset": 100,
        "view_count": 500,
        "duration": 12.0,
        "created_at": "2026-10-19T10:00:00+00:00",
        "score": 10.0,
    }
    record.update(overrides)
    return record
