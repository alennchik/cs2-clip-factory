"""SQLite storage. One file, no server; every pipeline step is idempotent against it."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipfactory.models import ClipStatus, RenderStatus

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    clip_id           TEXT PRIMARY KEY,
    broadcaster_id    TEXT NOT NULL,
    broadcaster_login TEXT NOT NULL,
    broadcaster_name  TEXT NOT NULL DEFAULT '',
    title             TEXT NOT NULL DEFAULT '',
    url               TEXT NOT NULL,
    thumbnail_url     TEXT NOT NULL DEFAULT '',
    game_id           TEXT NOT NULL DEFAULT '',
    language          TEXT NOT NULL DEFAULT '',
    video_id          TEXT NOT NULL DEFAULT '',
    vod_offset        INTEGER,
    view_count        INTEGER NOT NULL DEFAULT 0,
    duration          REAL NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    score             REAL NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'discovered',
    error             TEXT,
    discovered_at     TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clips_status_score ON clips(status, score DESC);

CREATE TABLE IF NOT EXISTS media (
    clip_id        TEXT PRIMARY KEY REFERENCES clips(clip_id) ON DELETE CASCADE,
    path           TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    bytes          INTEGER NOT NULL,
    width          INTEGER NOT NULL,
    height         INTEGER NOT NULL,
    fps            REAL NOT NULL,
    duration       REAL NOT NULL,
    has_audio      INTEGER NOT NULL,
    method         TEXT NOT NULL,
    permission_ref TEXT NOT NULL,
    fetched_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_sha ON media(sha256);

CREATE TABLE IF NOT EXISTS transcripts (
    clip_id    TEXT PRIMARY KEY REFERENCES clips(clip_id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    backend    TEXT NOT NULL,
    model      TEXT NOT NULL,
    language   TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS renders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id             TEXT NOT NULL REFERENCES clips(clip_id) ON DELETE CASCADE,
    hook_text           TEXT NOT NULL DEFAULT '',
    hook_options        TEXT NOT NULL DEFAULT '[]',
    layout              TEXT NOT NULL,
    start               REAL NOT NULL DEFAULT 0,
    duration            REAL NOT NULL DEFAULT 0,
    path                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft',
    telegram_message_id INTEGER,
    export_path         TEXT,
    created_at          TEXT NOT NULL,
    decided_at          TEXT,
    posted_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_renders_status ON renders(status);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    clip_id   TEXT,
    render_id INTEGER,
    detail    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CLIP_FIELDS = (
    "clip_id",
    "broadcaster_id",
    "broadcaster_login",
    "broadcaster_name",
    "title",
    "url",
    "thumbnail_url",
    "game_id",
    "language",
    "video_id",
    "vod_offset",
    "view_count",
    "duration",
    "created_at",
    "score",
)
MEDIA_FIELDS = (
    "path",
    "sha256",
    "bytes",
    "width",
    "height",
    "fps",
    "duration",
    "has_audio",
    "method",
    "permission_ref",
)
RENDER_UPDATABLE = {
    "hook_text",
    "hook_options",
    "status",
    "telegram_message_id",
    "export_path",
    "decided_at",
    "posted_at",
    "path",
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


# --- clips -------------------------------------------------------------------------------


def upsert_clip(conn: sqlite3.Connection, clip: Mapping[str, Any]) -> bool:
    """Insert a clip or refresh its metadata. Never touches status. Returns True if new."""
    exists = conn.execute("SELECT 1 FROM clips WHERE clip_id = ?", (clip["clip_id"],)).fetchone()
    now = utcnow()
    values = [clip.get(f) for f in CLIP_FIELDS]
    placeholders = ", ".join("?" for _ in CLIP_FIELDS)
    conn.execute(
        f"""
        INSERT INTO clips ({", ".join(CLIP_FIELDS)}, discovered_at, updated_at)
        VALUES ({placeholders}, ?, ?)
        ON CONFLICT(clip_id) DO UPDATE SET
            view_count = excluded.view_count,
            score = excluded.score,
            title = excluded.title,
            broadcaster_name = excluded.broadcaster_name,
            thumbnail_url = excluded.thumbnail_url,
            updated_at = excluded.updated_at
        """,
        (*values, now, now),
    )
    return exists is None


def get_clip(conn: sqlite3.Connection, clip_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM clips WHERE clip_id = ?", (clip_id,)).fetchone()


def list_clips(
    conn: sqlite3.Connection,
    statuses: Sequence[str] | None = None,
    login: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM clips WHERE 1 = 1"
    params: list[Any] = []
    if statuses:
        sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
        params.extend(statuses)
    if login:
        sql += " AND broadcaster_login = ?"
        params.append(login.lower())
    sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def set_clip_status(
    conn: sqlite3.Connection, clip_id: str, status: ClipStatus, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE clips SET status = ?, error = ?, updated_at = ? WHERE clip_id = ?",
        (str(status), error, utcnow(), clip_id),
    )


def processed_vod_spans(conn: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """(video_id, vod_offset, clip_id) for clips already in the pipeline, for overlap checks."""
    rows = conn.execute(
        """
        SELECT video_id, vod_offset, clip_id FROM clips
        WHERE video_id != '' AND vod_offset IS NOT NULL
          AND status IN (?, ?, ?)
        """,
        (str(ClipStatus.FETCHED), str(ClipStatus.TRANSCRIBED), str(ClipStatus.RENDERED)),
    ).fetchall()
    return [(r["video_id"], int(r["vod_offset"]), r["clip_id"]) for r in rows]


# --- media / transcripts -----------------------------------------------------------------


def save_media(conn: sqlite3.Connection, clip_id: str, **fields: Any) -> None:
    missing = [f for f in MEDIA_FIELDS if f not in fields]
    if missing:
        raise ValueError(f"missing media fields: {missing}")
    values = [fields[f] for f in MEDIA_FIELDS]
    conn.execute(
        f"""
        INSERT OR REPLACE INTO media (clip_id, {", ".join(MEDIA_FIELDS)}, fetched_at)
        VALUES (?, {", ".join("?" for _ in MEDIA_FIELDS)}, ?)
        """,
        (clip_id, *values, utcnow()),
    )


def get_media(conn: sqlite3.Connection, clip_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media WHERE clip_id = ?", (clip_id,)).fetchone()


def find_media_by_sha(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media WHERE sha256 = ?", (sha256,)).fetchone()


def list_media(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM media ORDER BY fetched_at").fetchall()


def latest_media_for_login(conn: sqlite3.Connection, login: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT media.* FROM media JOIN clips USING (clip_id)
        WHERE clips.broadcaster_login = ?
        ORDER BY media.fetched_at DESC LIMIT 1
        """,
        (login.lower(),),
    ).fetchone()


def save_transcript(
    conn: sqlite3.Connection, clip_id: str, path: str, backend: str, model: str, language: str
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO transcripts (clip_id, path, backend, model, language, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (clip_id, path, backend, model, language, utcnow()),
    )


def get_transcript(conn: sqlite3.Connection, clip_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM transcripts WHERE clip_id = ?", (clip_id,)).fetchone()


# --- renders -----------------------------------------------------------------------------


def create_render(
    conn: sqlite3.Connection,
    clip_id: str,
    *,
    hook_text: str,
    hook_options: Iterable[str],
    layout: str,
    start: float,
    duration: float,
    path: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO renders (clip_id, hook_text, hook_options, layout, start, duration, path,
                             status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clip_id,
            hook_text,
            json.dumps(list(hook_options), ensure_ascii=False),
            layout,
            start,
            duration,
            path,
            str(RenderStatus.DRAFT),
            utcnow(),
        ),
    )
    return int(cur.lastrowid)


def update_render(conn: sqlite3.Connection, render_id: int, **fields: Any) -> None:
    unknown = set(fields) - RENDER_UPDATABLE
    if unknown:
        raise ValueError(f"cannot update render fields: {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = [str(v) if isinstance(v, RenderStatus) else v for v in fields.values()]
    conn.execute(f"UPDATE renders SET {assignments} WHERE id = ?", (*values, render_id))


def get_render(conn: sqlite3.Connection, render_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()


def list_renders(
    conn: sqlite3.Connection, statuses: Sequence[str] | None = None, limit: int = 50
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM renders"
    params: list[Any] = []
    if statuses:
        sql += f" WHERE status IN ({', '.join('?' for _ in statuses)})"
        params.extend(statuses)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def hook_options(render: sqlite3.Row) -> list[str]:
    try:
        options = json.loads(render["hook_options"] or "[]")
    except json.JSONDecodeError:
        return []
    return [str(o) for o in options]


# --- audit log / key-value ---------------------------------------------------------------


def log_event(
    conn: sqlite3.Connection,
    kind: str,
    *,
    clip_id: str | None = None,
    render_id: int | None = None,
    detail: str = "",
) -> None:
    conn.execute(
        "INSERT INTO events (ts, kind, clip_id, render_id, detail) VALUES (?, ?, ?, ?, ?)",
        (utcnow(), kind, clip_id, render_id, detail),
    )


def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def latest_event_detail(conn: sqlite3.Connection, kind: str, render_id: int) -> str | None:
    row = conn.execute(
        "SELECT detail FROM events WHERE kind = ? AND render_id = ? ORDER BY id DESC LIMIT 1",
        (kind, render_id),
    ).fetchone()
    return str(row["detail"]) if row else None


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def status_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    clips = {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")
    }
    renders = {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM renders GROUP BY status")
    }
    return {"clips": clips, "renders": renders}
