"""`clipfactory doctor`: check the machine and configuration before the first run."""

from __future__ import annotations

import importlib.util
import platform
import re
import subprocess
import sys
from dataclasses import dataclass

from clipfactory.config import AppConfig
from clipfactory.render import RenderError, resolve_font
from clipfactory.settings import Settings

REQUIRED_FILTERS = (
    "loudnorm",
    "aresample",
    "vstack",
    "overlay",
    "boxblur",
    "scale",
    "crop",
    "split",
    "eq",
    "format",
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _has(listing: str, name: str) -> bool:
    return re.search(rf"\s{re.escape(name)}\s", listing) is not None


def check_tools(settings: Settings, cfg: AppConfig) -> list[Check]:
    checks: list[Check] = []
    py_ok = sys.version_info >= (3, 12)
    checks.append(Check("python", "ok" if py_ok else "fail", platform.python_version()))

    rc, out = _run([settings.ffmpeg_bin, "-hide_banner", "-version"])
    if rc != 0:
        checks.append(
            Check(
                "ffmpeg", "fail", f"not found ({settings.ffmpeg_bin}); macOS: brew install ffmpeg"
            )
        )
        return checks
    checks.append(Check("ffmpeg", "ok", out.splitlines()[0] if out else ""))
    rc, _ = _run([settings.ffprobe_bin, "-hide_banner", "-version"])
    checks.append(Check("ffprobe", "ok" if rc == 0 else "fail", settings.ffprobe_bin))

    _, filters = _run([settings.ffmpeg_bin, "-hide_banner", "-filters"])
    missing = [f for f in REQUIRED_FILTERS if not _has(filters, f)]
    checks.append(
        Check(
            "ffmpeg filters",
            "fail" if missing else "ok",
            f"missing: {', '.join(missing)} (try `brew install ffmpeg-full`)" if missing else "ok",
        )
    )
    _, encoders = _run([settings.ffmpeg_bin, "-hide_banner", "-encoders"])
    enc = cfg.render.encoder
    checks.append(
        Check(f"encoder {enc}", "ok" if _has(encoders, enc) else "fail", "render.encoder in config")
    )
    if not _has(encoders, "libx264"):
        checks.append(Check("encoder libx264", "warn", "needed for Telegram previews"))

    rc, out = _run([*settings.ytdlp_command(), "--version"])
    checks.append(
        Check(
            "yt-dlp", "ok" if rc == 0 else "fail", out.strip() if rc == 0 else "pip install yt-dlp"
        )
    )
    return checks


def check_assets(cfg: AppConfig) -> list[Check]:
    try:
        font = resolve_font(cfg.render.font)
    except RenderError as exc:
        return [Check("font", "fail", str(exc))]
    from PIL import ImageFont

    try:
        ImageFont.truetype(font, 40)
    except OSError as exc:
        return [Check("font", "fail", f"{font}: {exc}")]
    return [Check("font", "ok", font)]


def check_asr(cfg: AppConfig) -> list[Check]:
    backend = cfg.transcribe.backend
    if backend == "none":
        return [Check("speech-to-text", "warn", "backend none: hooks come from clip titles only")]
    module = "faster_whisper" if backend == "faster-whisper" else "mlx_whisper"
    extra = "asr" if backend == "faster-whisper" else "mlx"
    found = importlib.util.find_spec(module) is not None
    checks = [
        Check(
            f"speech-to-text ({backend})",
            "ok" if found else "fail",
            cfg.transcribe.model if found else f"pip install -e '.[{extra}]'",
        )
    ]
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        if backend == "faster-whisper":
            checks.append(Check("apple silicon", "warn", "transcribe.backend: mlx is much faster"))
        if cfg.render.encoder == "libx264":
            checks.append(
                Check("apple silicon", "warn", "render.encoder: h264_videotoolbox is faster")
            )
    return checks


def check_secrets(settings: Settings) -> list[Check]:
    def present(name: str, value: object) -> Check:
        return Check(name, "ok" if value else "fail", "set" if value else "missing in .env")

    return [
        present("TWITCH_CLIENT_ID", settings.twitch_client_id),
        present("TWITCH_CLIENT_SECRET", settings.twitch_client_secret),
        present("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token),
        Check(
            "TELEGRAM_CHAT_ID",
            "ok" if settings.telegram_chat_id else "fail",
            "set" if settings.telegram_chat_id else "run `clipfactory review chat-id`",
        ),
    ]


def check_streamers(cfg: AppConfig) -> list[Check]:
    if not cfg.streamers:
        return [Check("streamers", "fail", "no streamers in config/streamers.yaml")]
    checks = []
    for s in cfg.streamers:
        notes: list[str] = []
        status = "ok"
        if s.permission != "explicit":
            status = "warn"
            notes.append(f"permission: {s.permission} — paused, clips are not downloaded")
        else:
            notes.append(
                "download enabled by local config; redistribution/monetization not verified"
            )
        if s.webcam is None and s.layout != "full":
            status = "warn" if status == "ok" else status
            notes.append("no webcam box yet — full layout until you run calibrate")
        checks.append(Check(f"streamer {s.login}", status, "; ".join(notes) or "ready"))
    return checks
