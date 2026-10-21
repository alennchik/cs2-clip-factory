"""Insert an optional WebM sponsor segment between render and Telegram review."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from clipfactory import media


class SponsorError(RuntimeError):
    """The promotional asset could not be rendered or inserted."""


@dataclass(frozen=True)
class SponsorResult:
    at: float
    duration: float


def choose_insertion_time(
    duration: float,
    envelope: np.ndarray,
    *,
    window: float = 0.1,
    start_fraction: float = 0.35,
    end_fraction: float = 0.60,
    fps: float = 30.0,
) -> float:
    """Pick a quieter cut between 35% and 60% of the clip, snapped to a frame.

    This is an audio-based heuristic, not an AI semantic kill/clutch detector.
    """
    if duration <= 0 or window <= 0 or fps <= 0:
        raise SponsorError("invalid duration, envelope window or frame rate")
    if not 0 < start_fraction < end_fraction < 1:
        raise SponsorError("invalid placement window")
    target = duration * 0.45
    if not len(envelope):
        return round(round(target * fps) / fps, 3)

    lo = max(1, int(duration * start_fraction / window))
    hi = min(len(envelope) - 1, int(duration * end_fraction / window))
    if hi <= lo:
        return round(round(target * fps) / fps, 3)

    half = max(1, round(0.35 / window))
    candidates: list[tuple[float, float, int]] = []
    for i in range(lo, hi + 1):
        local = envelope[max(0, i - half) : min(len(envelope), i + half + 1)]
        if len(local):
            candidates.append((float(np.quantile(local, 0.75)), abs(i * window - target), i))
    best = min(candidates)[2] if candidates else round(target / window)
    return round(round((best * window) * fps) / fps, 3)


def _run(cmd: list[str], what: str, *, timeout: int = 900) -> None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SponsorError(f"{what}: {exc}") from exc
    if proc.returncode:
        raise SponsorError(f"{what}: {media.tail(proc.stderr)}")


def _encode_args(encoder: str, crf: int, bitrate: str) -> list[str]:
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(crf),
        "-maxrate",
        bitrate,
        "-bufsize",
        bitrate,
    ]


def insert_sponsor(
    video: Path,
    asset: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    encoder: str = "libx264",
    crf: int = 20,
    max_bitrate: str = "7M",
    min_clip_seconds: float = 12.0,
) -> SponsorResult | None:
    """Insert a full banner once, keeping all original gameplay and audio intact.

    The original `video` is replaced atomically only after a complete, verified
    output is ready. Short clips are left unchanged.
    """
    if not asset.is_file():
        raise SponsorError(f"sponsor asset not found: {asset}")

    original = media.probe(video, ffprobe_bin)
    if original.duration < min_clip_seconds:
        return None
    if original.width != 1080 or original.height != 1920 or not original.has_audio:
        raise SponsorError("sponsor insertion needs a 1080x1920 render with audio")

    banner = media.probe(asset, ffprobe_bin)
    if banner.duration <= 0.1:
        raise SponsorError("sponsor asset has no valid duration")

    try:
        levels = media.audio_envelope_db(video, ffmpeg_bin)
    except media.MediaError:
        levels = np.zeros(0, dtype=np.float32)

    fps = max(24.0, min(original.fps or 30.0, 60.0))
    fps_text = f"{fps:.3f}"
    at = choose_insertion_time(original.duration, levels, fps=fps)
    seconds = banner.duration

    with tempfile.TemporaryDirectory(prefix="sponsor-", dir=video.parent) as scratch:
        tmp = Path(scratch)
        still = media.extract_frame(video, at, tmp / "frame.png", ffmpeg_bin)
        segment = tmp / "interlude.mp4"

        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            fps_text,
            "-i",
            str(still),
        ]
        # Explicit libvpx-vp9 decoding retains WebM alpha on supported FFmpeg builds.
        if banner.vcodec == "vp9":
            cmd += ["-c:v", "libvpx-vp9"]
        cmd += ["-i", str(asset)]
        if not banner.has_audio:
            cmd += [
                "-f",
                "lavfi",
                "-t",
                f"{seconds:.3f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]

        graph = (
            "[0:v]scale=270:480:force_original_aspect_ratio=increase,"
            "crop=270:480,boxblur=8:2,scale=1080:1920,"
            "eq=brightness=-0.14[bg];"
            f"[1:v]fps={fps_text},"
            "scale=1000:1700:force_original_aspect_ratio=decrease,"
            "format=rgba[ad];"
            "[bg][ad]overlay=(W-w)/2:(H-h)/2:shortest=1,"
            "format=yuv420p[v]"
        )
        audio_input = "1:a" if banner.has_audio else "2:a"
        cmd += [
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-map",
            audio_input,
            "-t",
            f"{seconds:.3f}",
            "-r",
            fps_text,
            *_encode_args(encoder, crf, max_bitrate),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(segment),
        ]
        _run(cmd, "building sponsor segment")

        cut = f"{at:.3f}"
        graph = (
            "[0:v]split=2[vp][vq];"
            f"[vp]trim=end={cut},setpts=PTS-STARTPTS,fps={fps_text},format=yuv420p[v0];"
            f"[vq]trim=start={cut},setpts=PTS-STARTPTS,fps={fps_text},format=yuv420p[v2];"
            "[0:a]asplit=2[ap][aq];"
            f"[ap]atrim=end={cut},asetpts=PTS-STARTPTS,aresample=48000[a0];"
            f"[aq]atrim=start={cut},asetpts=PTS-STARTPTS,aresample=48000[a2];"
            f"[1:v]fps={fps_text},format=yuv420p[v1];"
            "[1:a]aresample=48000[a1];"
            "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
        )
        combined = tmp / "combined.mp4"
        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(segment),
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-map",
            "[a]",
            *_encode_args(encoder, crf, max_bitrate),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(combined),
        ]
        _run(cmd, "combining original and sponsor")
        actual = media.probe(combined, ffprobe_bin).duration
        if abs(actual - (original.duration + seconds)) > 0.45:
            raise SponsorError("combined video duration does not match the expected total")
        combined.replace(video)

    return SponsorResult(at=at, duration=seconds)
