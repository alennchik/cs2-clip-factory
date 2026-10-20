import shutil
from pathlib import Path

import pytest

from clipfactory import db
from clipfactory.config import AppConfig, NormBox, RenderConfig, StreamerConfig
from clipfactory.fetch import FetchError, fetch_clip
from clipfactory.models import ClipStatus, RenderStatus
from clipfactory.pipeline import (
    export_render,
    post_caption,
    rerender,
    run_pipeline,
    select_candidates,
)
from clipfactory.transcribe import NullTranscriber

from .conftest import FONT, WEBCAM, clip_record, make_clip, needs_ffmpeg


def _config(**streamer) -> AppConfig:
    fields = {
        "login": "streamer_a",
        "display_name": "StreamerA",
        "permission": "explicit",
        "permission_proof": "permissions/streamer_a.png",
        "webcam": NormBox(x=WEBCAM[0], y=WEBCAM[1], w=WEBCAM[2], h=WEBCAM[3]),
    }
    fields.update(streamer)
    return AppConfig(
        render=RenderConfig(font=str(FONT), max_duration=45),
        streamers=[StreamerConfig(**fields)],
    )


def _copying_downloader(source: Path | dict[str, Path], calls: list[str]):
    def download(url: str, dest_dir: Path, basename: str) -> Path:
        calls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{basename}.mp4"
        shutil.copyfile(source[url] if isinstance(source, dict) else source, target)
        return target

    return download


def test_upsert_keeps_status_and_never_duplicates(conn):
    assert db.upsert_clip(conn, clip_record()) is True
    db.set_clip_status(conn, "Clip1", ClipStatus.RENDERED)
    assert db.upsert_clip(conn, clip_record(view_count=999)) is False
    row = db.get_clip(conn, "Clip1")
    assert row["status"] == ClipStatus.RENDERED and row["view_count"] == 999
    assert len(db.list_clips(conn)) == 1


def test_no_permission_means_no_download(conn, settings):
    db.upsert_clip(conn, clip_record())
    calls: list[str] = []
    cfg = _config(permission="unknown")
    outcome = fetch_clip(
        conn, settings, cfg, db.get_clip(conn, "Clip1"), _copying_downloader(Path("unused"), calls)
    )
    assert outcome.status == ClipStatus.SKIPPED
    assert calls == []
    assert "permission" in db.get_clip(conn, "Clip1")["error"]


def test_unknown_streamer_is_skipped(conn, settings):
    db.upsert_clip(conn, clip_record(login="someone_else"))
    outcome = fetch_clip(
        conn,
        settings,
        _config(),
        db.get_clip(conn, "Clip1"),
        _copying_downloader(Path("unused"), []),
    )
    assert outcome.status == ClipStatus.SKIPPED


def test_downloader_failure_marks_clip_failed(conn, settings):
    db.upsert_clip(conn, clip_record())

    def broken(url, dest, name):
        raise FetchError("network down")

    outcome = fetch_clip(conn, settings, _config(), db.get_clip(conn, "Clip1"), broken)
    assert outcome.status == ClipStatus.FAILED
    assert db.get_clip(conn, "Clip1")["status"] == ClipStatus.FAILED


def test_selection_skips_overlapping_clips_of_the_same_moment(conn):
    db.upsert_clip(conn, clip_record("A", score=50, vod_offset=100))
    db.upsert_clip(conn, clip_record("B", score=40, vod_offset=110))  # same moment, other clipper
    db.upsert_clip(conn, clip_record("C", score=30, vod_offset=400))
    db.upsert_clip(conn, clip_record("D", score=99, login="no_permission"))
    chosen = select_candidates(conn, _config(), limit=5)
    assert [r["clip_id"] for r in chosen] == ["A", "C"]
    assert db.get_clip(conn, "B")["status"] == ClipStatus.SKIPPED
    assert db.get_clip(conn, "D")["status"] == ClipStatus.DISCOVERED  # waits for permission


@needs_ffmpeg
def test_full_run_renders_exports_and_rerenders(conn, settings, clip_12s, tmp_path):
    db.upsert_clip(conn, clip_record("Clip1"))
    db.upsert_clip(conn, clip_record("Clip2", vod_offset=900, score=5))
    sources = {
        "https://clips.twitch.tv/Clip1": clip_12s,
        "https://clips.twitch.tv/Clip2": make_clip(tmp_path / "second.mp4", duration=6.0),
    }
    calls: list[str] = []
    cfg = _config()
    report = run_pipeline(
        conn,
        settings,
        cfg,
        downloader=_copying_downloader(sources, calls),
        transcriber=NullTranscriber(),
        limit=1,
    )
    assert report.selected == 1 and len(report.rendered) == 1
    assert calls == ["https://clips.twitch.tv/Clip1"]  # only the top clip was downloaded

    render = db.get_render(conn, report.rendered[0])
    assert render["status"] == RenderStatus.DRAFT and render["layout"] == "split"
    assert render["hook_text"] == "ЭЛЕКТРОНИК ЗАМАШНИЛ"  # title fallback (no speech)
    assert Path(render["path"]).is_file()
    media = db.get_media(conn, "Clip1")
    assert media["permission_ref"] == "permissions/streamer_a.png" and len(media["sha256"]) == 64

    # running again does not download or render the same clip twice
    again = run_pipeline(
        conn,
        settings,
        cfg,
        downloader=_copying_downloader(sources, calls),
        transcriber=NullTranscriber(),
        limit=1,
    )
    assert calls[-1] == "https://clips.twitch.tv/Clip2" and len(calls) == 2
    assert again.rendered and again.rendered[0] != report.rendered[0]

    new_id = rerender(conn, settings, cfg, report.rendered[0], "StreamerA: вот это да")
    assert db.get_render(conn, report.rendered[0])["status"] == RenderStatus.SUPERSEDED
    assert db.get_render(conn, new_id)["hook_text"] == "StreamerA: вот это да"

    exported = export_render(conn, settings, new_id)
    assert exported.is_file() and exported.parent == settings.ready_dir
    caption = exported.with_suffix(".txt").read_text(encoding="utf-8")
    assert "twitch.tv/streamer_a" in caption and "#cs2" in caption


def test_post_caption_format(conn):
    db.upsert_clip(conn, clip_record())
    text = post_caption(db.get_clip(conn, "Clip1"), "StreamerA: я бы замашнил")
    assert text.splitlines()[0] == "StreamerA: я бы замашнил"
    assert "Клип: twitch.tv/streamer_a" in text


@needs_ffmpeg
def test_identical_file_for_a_second_clip_is_skipped(conn, settings, clip_12s):
    db.upsert_clip(conn, clip_record("A", vod_offset=100))
    db.upsert_clip(conn, clip_record("B", vod_offset=900))
    cfg = _config()
    download = _copying_downloader(clip_12s, [])
    assert fetch_clip(conn, settings, cfg, db.get_clip(conn, "A"), download).status == "fetched"
    outcome = fetch_clip(conn, settings, cfg, db.get_clip(conn, "B"), download)
    assert outcome.status == ClipStatus.SKIPPED and "same file" in outcome.reason


@pytest.mark.parametrize("status", [ClipStatus.SKIPPED, ClipStatus.FAILED])
def test_status_counts(conn, status):
    db.upsert_clip(conn, clip_record())
    db.set_clip_status(conn, "Clip1", status, "why")
    assert db.status_counts(conn)["clips"] == {str(status): 1}
