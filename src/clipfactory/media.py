"""ffprobe/ffmpeg helpers: probing, checksums, loudness envelope, frame grabs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    vcodec: str
    acodec: str


def tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def parse_rate(text: str) -> float:
    num, _, den = text.partition("/")
    try:
        n, d = float(num), float(den or 1)
    except ValueError:
        return 0.0
    return n / d if d else 0.0


def probe(path: str | Path, ffprobe_bin: str = "ffprobe") -> Probe:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise MediaError(f"ffprobe not found: {ffprobe_bin}") from exc
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed for {path}: {tail(proc.stderr, 3)}")
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"no video stream in {path}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0.0)
    fps = parse_rate(str(video.get("avg_frame_rate") or "0/1")) or parse_rate(
        str(video.get("r_frame_rate") or "0/1")
    )
    return Probe(
        width=int(video["width"]),
        height=int(video["height"]),
        fps=round(fps, 3),
        duration=duration,
        has_audio=audio is not None,
        vcodec=str(video.get("codec_name", "")),
        acodec=str(audio.get("codec_name", "")) if audio else "",
    )


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def audio_envelope_db(
    path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
    *,
    window: float = 0.1,
    sample_rate: int = 16000,
) -> np.ndarray:
    """Loudness (dBFS RMS) per `window` seconds. Empty array if the file has no audio."""
    cmd = [
        ffmpeg_bin,
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise MediaError(f"audio decode failed: {tail(proc.stderr.decode(errors='replace'), 3)}")
    samples = np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0
    hop = int(sample_rate * window)
    frames = len(samples) // hop
    if frames == 0:
        return np.zeros(0, dtype=np.float32)
    blocks = samples[: frames * hop].reshape(frames, hop)
    rms = np.sqrt(np.mean(blocks**2, axis=1) + 1e-12)
    return (20.0 * np.log10(rms)).astype(np.float32)


def extract_frame(
    path: str | Path, at: float, out_png: str | Path, ffmpeg_bin: str = "ffmpeg"
) -> Path:
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{max(at, 0.0):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        str(out_png),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not out_png.is_file():
        raise MediaError(f"frame grab failed: {tail(proc.stderr, 3)}")
    return out_png


def integrated_loudness(path: str | Path, ffmpeg_bin: str = "ffmpeg") -> float:
    """Integrated loudness in LUFS (EBU R128), used by tests and `doctor`."""
    cmd = [ffmpeg_bin, "-nostdin", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise MediaError(f"ebur128 failed: {tail(proc.stderr, 3)}")
    summary = proc.stderr.rsplit("Summary:", 1)[-1]
    for line in summary.splitlines():
        line = line.strip()
        if line.startswith("I:"):
            return float(line.split()[1])
    raise MediaError("could not parse integrated loudness")
