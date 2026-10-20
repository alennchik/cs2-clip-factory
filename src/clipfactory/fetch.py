"""Step 2: download clip media — only for streamers with explicit, recorded permission.

Preferred official route (when you are the channel's editor): Helix "Get Clips Download".
Until then clips are fetched with yt-dlp, and only for `permission: explicit` streamers.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from clipfactory import db, media
from clipfactory.config import AppConfig
from clipfactory.models import ClipStatus
from clipfactory.settings import Settings

log = logging.getLogger(__name__)

Downloader = Callable[[str, Path, str], Path]


class FetchError(RuntimeError):
    pass


def safe_name(text: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return name[:120] or "clip"


def ytdlp_downloader(command: Sequence[str], *, timeout: int = 300) -> Downloader:
    def download(url: str, dest_dir: Path, basename: str) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            *command,
            "--no-playlist",
            "--no-progress",
            "--no-simulate",
            "-f",
            "best[ext=mp4]/best",
            "-o",
            str(dest_dir / f"{basename}.%(ext)s"),
            "--print",
            "after_move:filepath",
            url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise FetchError(f"yt-dlp not found ({command[0]}); pip install yt-dlp") from exc
        if proc.returncode != 0:
            raise FetchError(f"yt-dlp failed: {media.tail(proc.stderr, 4)}")
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            raise FetchError("yt-dlp did not report a file path")
        path = Path(lines[-1])
        if not path.is_file():
            raise FetchError(f"downloaded file not found: {path}")
        return path

    return download


@dataclass
class FetchOutcome:
    clip_id: str
    status: ClipStatus
    path: Path | None = None
    reason: str = ""


def permission_problem(cfg: AppConfig, login: str) -> str | None:
    streamer = cfg.streamer(login)
    if streamer is None:
        return "streamer is not in config/streamers.yaml"
    if not streamer.can_fetch:
        return f"permission is '{streamer.permission}' — need 'explicit' plus permission_proof"
    return None


def fetch_clip(
    conn: sqlite3.Connection,
    settings: Settings,
    cfg: AppConfig,
    clip: sqlite3.Row,
    downloader: Downloader,
) -> FetchOutcome:
    clip_id = clip["clip_id"]
    problem = permission_problem(cfg, clip["broadcaster_login"])
    if problem:
        db.set_clip_status(conn, clip_id, ClipStatus.SKIPPED, problem)
        db.log_event(conn, "fetch_skipped", clip_id=clip_id, detail=problem)
        return FetchOutcome(clip_id, ClipStatus.SKIPPED, reason=problem)

    streamer = cfg.streamer(clip["broadcaster_login"])
    assert streamer is not None
    try:
        path = downloader(clip["url"], settings.raw_dir, safe_name(clip_id))
        info = media.probe(path, settings.ffprobe_bin)
        if info.duration <= 0:
            raise FetchError("downloaded file has zero duration")
        digest = media.sha256_file(path)
        duplicate = db.find_media_by_sha(conn, digest)
        if duplicate is not None and duplicate["clip_id"] != clip_id:
            path.unlink(missing_ok=True)
            reason = f"same file as clip {duplicate['clip_id']}"
            db.set_clip_status(conn, clip_id, ClipStatus.SKIPPED, reason)
            db.log_event(conn, "fetch_duplicate", clip_id=clip_id, detail=reason)
            return FetchOutcome(clip_id, ClipStatus.SKIPPED, reason=reason)
        db.save_media(
            conn,
            clip_id,
            path=str(path),
            sha256=digest,
            bytes=path.stat().st_size,
            width=info.width,
            height=info.height,
            fps=info.fps,
            duration=info.duration,
            has_audio=int(info.has_audio),
            method="yt-dlp",
            permission_ref=streamer.permission_proof,
        )
        db.set_clip_status(conn, clip_id, ClipStatus.FETCHED)
        db.log_event(
            conn,
            "fetch",
            clip_id=clip_id,
            detail=json.dumps(
                {"path": str(path), "sha256": digest, "proof": streamer.permission_proof}
            ),
        )
        return FetchOutcome(clip_id, ClipStatus.FETCHED, path=path)
    except (FetchError, media.MediaError, subprocess.TimeoutExpired, OSError) as exc:
        db.set_clip_status(conn, clip_id, ClipStatus.FAILED, f"fetch: {exc}")
        db.log_event(conn, "fetch_failed", clip_id=clip_id, detail=str(exc))
        log.warning("fetch failed for %s: %s", clip_id, exc)
        return FetchOutcome(clip_id, ClipStatus.FAILED, reason=str(exc))
