"""Step 5: FFmpeg render of the 9:16 video (layout + text layers + loudness normalization)."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from clipfactory import layout as L
from clipfactory.config import NormBox, RenderConfig
from clipfactory.hooks import clean_line
from clipfactory.media import Probe, tail
from clipfactory.media import probe as probe_media
from clipfactory.textcard import (
    CAPTION_STYLE,
    CREDIT_STYLE,
    HOOK_STYLE,
    hex_to_rgb,
    render_text_layer,
)
from clipfactory.transcribe import Transcript

REPO_ROOT = Path(__file__).resolve().parents[2]
TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024
MAX_CAPTION_CHUNKS = 60


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Overlay:
    """A full-frame transparent PNG shown from `start` to `end` seconds (None = until the end)."""

    png: Path
    start: float = 0.0
    end: float | None = None

    def enable_expr(self) -> str:
        if self.start <= 0 and self.end is None:
            return ""
        end = "1e9" if self.end is None else f"{self.end:.3f}"
        return f":enable='between(t,{self.start:.3f},{end})'"


@dataclass
class RenderJob:
    src: Path
    out: Path
    probe: Probe
    layout: Literal["split", "full"]
    start: float = 0.0
    length: float | None = None
    webcam: NormBox | None = None
    game_center_x: float = 0.5
    overlays: list[Overlay] = field(default_factory=list)
    encoder: str = "libx264"
    crf: int = 20
    max_bitrate: str = "7M"
    loudness_lufs: float = -14.0
    fit_zoom: float = 1.0


@dataclass(frozen=True)
class RenderResult:
    path: Path
    start: float
    length: float
    layout: str


def _double_rate(rate: str) -> str:
    number = rate.rstrip("kKmM")
    unit = rate[len(number) :]
    return f"{float(number) * 2:g}{unit}"


def build_filtergraph(job: RenderJob) -> str:
    p = job.probe
    parts: list[str] = []
    if job.layout == "split":
        if job.webcam is None:
            raise RenderError("split layout needs a webcam box")
        game = L.game_box(p.width, p.height, job.game_center_x)
        cam = L.webcam_box(p.width, p.height, job.webcam)
        parts += [
            "[0:v]split=2[game][cam]",
            f"[game]{game.crop_filter()},scale={L.OUT_W}:{L.TOP_H}:flags=lanczos,setsar=1[top]",
            f"[cam]{cam.crop_filter()},scale={L.OUT_W}:{L.BOTTOM_H}:flags=lanczos,setsar=1[bottom]",
            "[top][bottom]vstack=inputs=2[base]",
        ]
    else:
        fit_w, fit_h = L.fit_size(p.width, p.height, job.fit_zoom)
        fg = L.full_foreground_box(p.width, p.height, job.fit_zoom)
        overflow = f",crop={fg.w}:{fg.h}" if (fit_w, fit_h) != (fg.w, fg.h) else ""
        parts += [
            "[0:v]split=2[bgsrc][fgsrc]",
            "[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            f"boxblur=8:2,scale={L.OUT_W}:{L.OUT_H},eq=brightness=-0.08,setsar=1[bg]",
            f"[fgsrc]scale={fit_w}:{fit_h}:flags=lanczos{overflow},setsar=1[fg]",
            f"[bg][fg]overlay={fg.x}:{fg.y}[base]",
        ]
    last = "base"
    for i, overlay in enumerate(job.overlays, start=1):
        parts.append(f"[{last}][{i}:v]overlay=0:0{overlay.enable_expr()}[ov{i}]")
        last = f"ov{i}"
    parts.append(f"[{last}]format=yuv420p[vout]")
    if p.has_audio:
        parts.append(f"[0:a]loudnorm=I={job.loudness_lufs:g}:TP=-1.5:LRA=11,aresample=48000[aout]")
    return ";".join(parts)


def build_command(job: RenderJob, ffmpeg_bin: str = "ffmpeg") -> list[str]:
    length = job.length if job.length is not None else max(job.probe.duration - job.start, 0.1)
    cmd = [ffmpeg_bin, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if job.start > 0:
        cmd += ["-ss", f"{job.start:.3f}"]
    cmd += ["-t", f"{length:.3f}", "-i", str(job.src)]
    for overlay in job.overlays:
        cmd += ["-i", str(overlay.png)]
    if not job.probe.has_audio:
        cmd += [
            "-f",
            "lavfi",
            "-t",
            f"{length:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
    cmd += ["-filter_complex", build_filtergraph(job), "-map", "[vout]"]
    cmd += ["-map", "[aout]"] if job.probe.has_audio else ["-map", f"{len(job.overlays) + 1}:a"]
    if job.probe.fps > 60.5:
        cmd += ["-r", "60"]
    if job.encoder == "h264_videotoolbox":
        cmd += ["-c:v", "h264_videotoolbox", "-b:v", job.max_bitrate, "-profile:v", "high"]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(job.crf),
            "-maxrate",
            job.max_bitrate,
            "-bufsize",
            _double_rate(job.max_bitrate),
            "-profile:v",
            "high",
        ]
    cmd += [
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(job.out),
    ]
    return cmd


def run_render(job: RenderJob, ffmpeg_bin: str = "ffmpeg", timeout: int = 900) -> Path:
    job.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = job.out.with_name(job.out.stem + ".part.mp4")
    cmd = build_command(replace(job, out=tmp), ffmpeg_bin)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RenderError(f"ffmpeg not found: {ffmpeg_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise RenderError(f"ffmpeg timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RenderError(f"ffmpeg failed:\n{tail(proc.stderr)}")
    tmp.replace(job.out)
    return job.out


def caption_chunks(
    transcript: Transcript,
    start: float,
    length: float,
    *,
    max_words: int = 3,
    max_span: float = 1.4,
    max_gap: float = 0.6,
) -> list[tuple[str, float, float]]:
    """Group words into short on-screen chunks, timed relative to the rendered window."""
    words = [w for w in transcript.words() if w.text and w.end > start and w.start < start + length]
    groups: list[list] = []
    current: list = []
    for word in words:
        if current and (
            len(current) >= max_words
            or word.end - current[0].start > max_span
            or word.start - current[-1].end > max_gap
        ):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)

    chunks: list[tuple[str, float, float]] = []
    for group in groups:
        text = clean_line(" ".join(w.text for w in group))
        if not text:
            continue
        t0 = max(group[0].start - start, 0.0)
        t1 = min(max(group[-1].end - start, t0 + 0.3), length)
        chunks.append((text, round(t0, 3), round(t1, 3)))
    # close tiny gaps between chunks so captions don't flicker
    for i in range(len(chunks) - 1):
        text, t0, t1 = chunks[i]
        next_start = chunks[i + 1][1]
        if 0 < next_start - t1 < 0.3:
            chunks[i] = (text, t0, next_start)
    return chunks[:MAX_CAPTION_CHUNKS]


def resolve_font(font: str) -> str:
    for candidate in (Path(font), REPO_ROOT / font):
        if candidate.is_file():
            return str(candidate)
    raise RenderError(f"font not found: {font}")


def render_video(
    src: Path,
    out: Path,
    *,
    cfg: RenderConfig,
    layout: str,
    webcam: NormBox | None,
    game_center_x: float = 0.5,
    hook_text: str = "",
    credit_text: str = "",
    transcript: Transcript | None = None,
    start: float | None = None,
    end: float | None = None,
    focus: tuple[float, float] | None = None,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    work_dir: Path | None = None,
) -> RenderResult:
    info = probe_media(src, ffprobe_bin)
    if start is not None or end is not None:
        s = max(start or 0.0, 0.0)
        e = min(end if end is not None else info.duration, info.duration)
        if e - s < 1.0:
            raise RenderError("trim window is shorter than 1 second")
        length = e - s
    else:
        s, length = L.choose_window(info.duration, cfg.max_duration, focus)

    chosen: Literal["split", "full"] = (
        "split" if layout == "split" and webcam is not None else "full"
    )
    font_path = resolve_font(cfg.font)
    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="render-", dir=work_dir) as tmp_name:
        tmp = Path(tmp_name)
        overlays: list[Overlay] = []
        if hook_text:
            png = render_text_layer(
                hook_text,
                tmp / "hook.png",
                font_path=font_path,
                style=replace(HOOK_STYLE, fill=hex_to_rgb(cfg.hook_color)),
            )
            until = (
                None if cfg.hook_seconds <= 0 or cfg.hook_seconds >= length else cfg.hook_seconds
            )
            overlays.append(Overlay(png, 0.0, until))
        if cfg.captions and transcript is not None:
            top = L.captions_top(chosen, info.width, info.height, cfg.fit_zoom)
            for i, (text, t0, t1) in enumerate(caption_chunks(transcript, s, length)):
                png = render_text_layer(
                    text,
                    tmp / f"caption_{i:03d}.png",
                    font_path=font_path,
                    top=top,
                    style=CAPTION_STYLE,
                )
                overlays.append(Overlay(png, t0, t1))
        if credit_text:
            png = render_text_layer(
                credit_text,
                tmp / "credit.png",
                font_path=font_path,
                top=L.credit_top(chosen, info.width, info.height, cfg.fit_zoom),
                max_width=L.OUT_W - 80,
                style=CREDIT_STYLE,
                align="left",
                left=36,
            )
            overlays.append(Overlay(png))

        job = RenderJob(
            src=src,
            out=out,
            probe=info,
            layout=chosen,
            start=s,
            length=length,
            webcam=webcam if chosen == "split" else None,
            game_center_x=game_center_x,
            overlays=overlays,
            encoder=cfg.encoder,
            crf=cfg.crf,
            max_bitrate=cfg.max_bitrate,
            loudness_lufs=cfg.loudness_lufs,
            fit_zoom=cfg.fit_zoom,
        )
        run_render(job, ffmpeg_bin)
    return RenderResult(out, round(s, 3), round(length, 3), chosen)


def make_preview(src: Path, dst: Path, ffmpeg_bin: str = "ffmpeg") -> Path:
    """Smaller copy for Telegram when the full render is over the bot upload limit."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
        "-vf", "scale=720:1280", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RenderError(f"preview failed:\n{tail(proc.stderr)}")
    return dst
