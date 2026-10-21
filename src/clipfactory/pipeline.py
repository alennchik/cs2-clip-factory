"""Glue: select -> fetch -> transcribe -> hooks -> render -> export. Each step is idempotent."""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from clipfactory import db, media
from clipfactory.config import AppConfig
from clipfactory.discover import ClipSource, DiscoverReport, discover
from clipfactory.fetch import Downloader, fetch_clip, permission_problem, safe_name
from clipfactory.hooks import HookCandidate, suggest_hooks
from clipfactory.models import ClipStatus, RenderStatus
from clipfactory.render import RenderError, render_video
from clipfactory.settings import Settings
from clipfactory.transcribe import Transcriber, Transcript, language_for

log = logging.getLogger(__name__)

OVERLAP_SECONDS = 30


class PipelineError(RuntimeError):
    pass


# --- selection ---------------------------------------------------------------------------


def find_overlap(clip: sqlite3.Row, spans: list[tuple[str, int, str]]) -> str | None:
    """Two clips of the same VOD moment (popular plays get clipped many times)."""
    if not clip["video_id"] or clip["vod_offset"] is None:
        return None
    for video_id, offset, other_id in spans:
        if (
            video_id == clip["video_id"]
            and other_id != clip["clip_id"]
            and abs(offset - int(clip["vod_offset"])) < OVERLAP_SECONDS
        ):
            return other_id
    return None


def select_candidates(conn: sqlite3.Connection, cfg: AppConfig, limit: int) -> list[sqlite3.Row]:
    spans = db.processed_vod_spans(conn)
    chosen: list[sqlite3.Row] = []
    for clip in db.list_clips(conn, statuses=[ClipStatus.DISCOVERED], limit=max(limit * 10, 50)):
        if permission_problem(cfg, clip["broadcaster_login"]):
            continue  # stays "discovered": picked up once permission is recorded
        other = find_overlap(clip, spans)
        if other:
            db.set_clip_status(conn, clip["clip_id"], ClipStatus.SKIPPED, f"overlaps clip {other}")
            continue
        chosen.append(clip)
        if clip["video_id"] and clip["vod_offset"] is not None:
            spans.append((clip["video_id"], int(clip["vod_offset"]), clip["clip_id"]))
        if len(chosen) >= limit:
            break
    return chosen


# --- transcripts and hooks ---------------------------------------------------------------


def load_transcript(conn: sqlite3.Connection, clip_id: str) -> Transcript | None:
    row = db.get_transcript(conn, clip_id)
    if row is None or not Path(row["path"]).is_file():
        return None
    return Transcript.load(row["path"])


def transcribe_clip(
    conn: sqlite3.Connection,
    settings: Settings,
    cfg: AppConfig,
    clip: sqlite3.Row,
    transcriber: Transcriber,
) -> Transcript:
    clip_id = clip["clip_id"]
    m = db.get_media(conn, clip_id)
    if m is None:
        raise PipelineError(f"{clip_id}: no media, run fetch first")
    streamer = cfg.streamer(clip["broadcaster_login"])
    transcript = transcriber.transcribe(Path(m["path"]), language_for(cfg.transcribe, streamer))
    path = transcript.save(settings.transcripts_dir / f"{safe_name(clip_id)}.json")
    db.save_transcript(
        conn, clip_id, str(path), transcript.backend, transcript.model, transcript.language
    )
    db.set_clip_status(conn, clip_id, ClipStatus.TRANSCRIBED)
    db.log_event(conn, "transcribe", clip_id=clip_id, detail=f"{len(transcript.segments)} segments")
    return transcript


def hook_candidates_for(
    conn: sqlite3.Connection,
    settings: Settings,
    cfg: AppConfig,
    clip: sqlite3.Row,
    transcript: Transcript | None,
) -> list[HookCandidate]:
    m = db.get_media(conn, clip["clip_id"])
    envelope = None
    if m is not None and m["has_audio"]:
        try:
            envelope = media.audio_envelope_db(m["path"], settings.ffmpeg_bin)
        except media.MediaError as exc:
            log.warning("loudness analysis failed for %s: %s", clip["clip_id"], exc)
    streamer = cfg.streamer(clip["broadcaster_login"])
    speaker = streamer.speaker if streamer else clip["broadcaster_name"]
    return suggest_hooks(transcript, title=clip["title"], speaker=speaker, envelope=envelope)


# --- render / re-render / export ---------------------------------------------------------


def render_clip(
    conn: sqlite3.Connection,
    settings: Settings,
    cfg: AppConfig,
    clip: sqlite3.Row,
    *,
    hook_text: str | None = None,
    hook_options: list[str] | None = None,
    start: float | None = None,
    end: float | None = None,
) -> int:
    clip_id = clip["clip_id"]
    m = db.get_media(conn, clip_id)
    if m is None or not Path(m["path"]).is_file():
        raise PipelineError(f"{clip_id}: media file missing, run fetch first")
    streamer = cfg.streamer(clip["broadcaster_login"])
    transcript = load_transcript(conn, clip_id)

    focus = None
    if hook_options is None:
        candidates = hook_candidates_for(conn, settings, cfg, clip, transcript)
        hook_options = [c.text for c in candidates]
        if candidates and candidates[0].source == "speech" and hook_text is None:
            focus = (candidates[0].start, candidates[0].end)
    if hook_text is None:
        hook_text = hook_options[0] if hook_options else ""

    out = settings.renders_dir / f"{safe_name(clip_id)}_{uuid.uuid4().hex[:8]}.mp4"
    result = render_video(
        Path(m["path"]),
        out,
        cfg=cfg.render,
        layout=streamer.resolved_layout if streamer else "full",
        webcam=streamer.webcam if streamer else None,
        game_center_x=streamer.game_center_x if streamer else 0.5,
        hook_text=hook_text,
        credit_text=f"twitch.tv/{clip['broadcaster_login']}" if cfg.render.credit else "",
        transcript=transcript,
        start=start,
        end=end,
        focus=focus,
        ffmpeg_bin=settings.ffmpeg_bin,
        ffprobe_bin=settings.ffprobe_bin,
        work_dir=settings.work_dir,
    )
    render_id = db.create_render(
        conn,
        clip_id,
        hook_text=hook_text,
        hook_options=hook_options,
        layout=result.layout,
        start=result.start,
        duration=result.length,
        path=str(result.path),
    )
    db.set_clip_status(conn, clip_id, ClipStatus.RENDERED)
    db.log_event(
        conn,
        "render",
        clip_id=clip_id,
        render_id=render_id,
        detail=json.dumps(
            {
                "layout": result.layout,
                "start": result.start,
                "length": result.length,
                "sponsor_at": result.sponsor_at,
                "sponsor_duration": result.sponsor_duration,
            },
        ),
    )
    return render_id


def rerender(
    conn: sqlite3.Connection, settings: Settings, cfg: AppConfig, render_id: int, hook_text: str
) -> int:
    old = db.get_render(conn, render_id)
    if old is None:
        raise PipelineError(f"render #{render_id} not found")
    clip = db.get_clip(conn, old["clip_id"])
    if clip is None:
        raise PipelineError(f"clip {old['clip_id']} not found")
    new_id = render_clip(
        conn,
        settings,
        cfg,
        clip,
        hook_text=hook_text,
        hook_options=db.hook_options(old),
        start=float(old["start"]),
        end=float(old["start"]) + float(old["duration"]),
    )
    db.update_render(conn, render_id, status=RenderStatus.SUPERSEDED)
    return new_id


def post_caption(clip: sqlite3.Row, hook_text: str) -> str:
    login = clip["broadcaster_login"]
    tag = re.sub(r"[^0-9A-Za-z_]", "", login)
    lines = [hook_text or clip["title"], "", f"Клип: twitch.tv/{login}", f"#cs2 #кс2 #{tag}"]
    return "\n".join(lines).strip() + "\n"


def export_render(conn: sqlite3.Connection, settings: Settings, render_id: int) -> Path:
    render = db.get_render(conn, render_id)
    if render is None:
        raise PipelineError(f"render #{render_id} not found")
    clip = db.get_clip(conn, render["clip_id"])
    assert clip is not None
    settings.ready_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    name = f"{stamp}_{clip['broadcaster_login']}_{safe_name(clip['clip_id'])}_{render_id}.mp4"
    dst = settings.ready_dir / name
    shutil.copy2(render["path"], dst)
    dst.with_suffix(".txt").write_text(post_caption(clip, render["hook_text"]), encoding="utf-8")
    db.log_event(conn, "export", clip_id=clip["clip_id"], render_id=render_id, detail=str(dst))
    return dst


# --- full run ----------------------------------------------------------------------------


def process_clip(
    conn: sqlite3.Connection,
    settings: Settings,
    cfg: AppConfig,
    clip: sqlite3.Row,
    *,
    downloader: Downloader,
    transcriber: Transcriber,
) -> int | None:
    clip_id = clip["clip_id"]
    outcome = fetch_clip(conn, settings, cfg, clip, downloader)
    if outcome.status != ClipStatus.FETCHED:
        return None
    clip = db.get_clip(conn, clip_id)
    try:
        transcribe_clip(conn, settings, cfg, clip, transcriber)
    except Exception as exc:  # noqa: BLE001 - ASR failure must not block the render
        db.log_event(conn, "transcribe_failed", clip_id=clip_id, detail=str(exc))
        log.warning("transcription failed for %s (continuing with title hooks): %s", clip_id, exc)
    try:
        return render_clip(conn, settings, cfg, db.get_clip(conn, clip_id))
    except (RenderError, media.MediaError, PipelineError, OSError) as exc:
        db.set_clip_status(conn, clip_id, ClipStatus.FAILED, f"render: {exc}")
        db.log_event(conn, "render_failed", clip_id=clip_id, detail=str(exc))
        log.warning("render failed for %s: %s", clip_id, exc)
        return None


@dataclass
class PipelineReport:
    discovered: DiscoverReport | None = None
    selected: int = 0
    rendered: list[int] = field(default_factory=list)
    not_rendered: list[str] = field(default_factory=list)


def run_pipeline(
    conn: sqlite3.Connection,
    settings: Settings,
    cfg: AppConfig,
    *,
    downloader: Downloader,
    transcriber: Transcriber,
    source: ClipSource | None = None,
    limit: int | None = None,
) -> PipelineReport:
    settings.ensure_dirs()
    report = PipelineReport()
    if source is not None:
        report.discovered = discover(conn, source, cfg)
    candidates = select_candidates(conn, cfg, limit or cfg.pipeline.clips_per_run)
    report.selected = len(candidates)
    for clip in candidates:
        render_id = process_clip(
            conn, settings, cfg, clip, downloader=downloader, transcriber=transcriber
        )
        if render_id is None:
            report.not_rendered.append(clip["clip_id"])
        else:
            report.rendered.append(render_id)
    return report
