"""Step 1: find fresh clips for whitelisted streamers and store ranked metadata."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from clipfactory import db
from clipfactory.config import AppConfig
from clipfactory.ranking import score_clip
from clipfactory.twitch import TwitchClip, TwitchError, TwitchUser

log = logging.getLogger(__name__)


class ClipSource(Protocol):
    def get_users(self, logins: list[str]) -> dict[str, TwitchUser]: ...

    def get_game_id(self, name: str) -> str | None: ...

    def iter_clips(
        self, broadcaster_id: str, started_at: datetime, ended_at: datetime, *, max_pages: int
    ): ...


@dataclass
class DiscoverReport:
    new: int = 0
    updated: int = 0
    filtered: int = 0
    missing_streamers: list[str] = field(default_factory=list)


def clip_row(clip: TwitchClip, login: str, score: float) -> dict[str, object]:
    return {
        "clip_id": clip.id,
        "broadcaster_id": clip.broadcaster_id,
        "broadcaster_login": login,
        "broadcaster_name": clip.broadcaster_name or login,
        "title": clip.title,
        "url": clip.url,
        "thumbnail_url": clip.thumbnail_url,
        "game_id": clip.game_id,
        "language": clip.language,
        "video_id": clip.video_id,
        "vod_offset": clip.vod_offset,
        "view_count": clip.view_count,
        "duration": clip.duration,
        "created_at": clip.created_at.isoformat(timespec="seconds"),
        "score": score,
    }


def discover(
    conn: sqlite3.Connection,
    source: ClipSource,
    cfg: AppConfig,
    *,
    now: datetime | None = None,
) -> DiscoverReport:
    now = now or datetime.now(UTC)
    report = DiscoverReport()
    if not cfg.streamers:
        return report

    users = source.get_users([s.login for s in cfg.streamers])
    game_id: str | None = None
    if cfg.discovery.game_only:
        game_id = source.get_game_id(cfg.discovery.game_name)
        if game_id is None:
            raise TwitchError(f"Twitch category not found: {cfg.discovery.game_name!r}")

    started = now - timedelta(hours=cfg.discovery.lookback_hours)
    for streamer in cfg.streamers:
        user = users.get(streamer.login)
        if user is None:
            report.missing_streamers.append(streamer.login)
            log.warning("Twitch user not found: %s", streamer.login)
            continue
        for clip in source.iter_clips(user.id, started, now, max_pages=cfg.discovery.max_pages):
            if game_id is not None and clip.game_id != game_id:
                report.filtered += 1
                continue
            if clip.view_count < cfg.discovery.min_views:
                report.filtered += 1
                continue
            score = score_clip(
                clip.view_count, clip.created_at, clip.duration, now=now, weight=streamer.weight
            )
            if db.upsert_clip(conn, clip_row(clip, streamer.login, score)):
                report.new += 1
            else:
                report.updated += 1

    db.log_event(conn, "discover", detail=json.dumps(asdict(report)))
    return report
