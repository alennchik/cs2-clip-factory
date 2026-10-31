"""Operational regressions reproduced by the engineering audit."""

import hashlib

import pytest

from clipfactory import db
from clipfactory.config import AppConfig, StreamerConfig
from clipfactory.models import ClipStatus
from clipfactory.pipeline import run_pipeline, select_candidates
from clipfactory.transcribe import NullTranscriber

from .conftest import clip_record


def cfg():
    return AppConfig(streamers=[StreamerConfig(login="streamer_a")])


def test_ineligible_top_candidates_do_not_starve_allowed_streamer(conn):
    for i in range(55):
        db.upsert_clip(conn, clip_record(f"Blocked{i}", login="blocked", score=100 + i))
    db.upsert_clip(conn, clip_record("Allowed", score=1))
    assert [r["clip_id"] for r in select_candidates(conn, cfg(), 1)] == ["Allowed"]


@pytest.mark.parametrize("status", [ClipStatus.FETCHED, ClipStatus.TRANSCRIBED])
def test_interrupted_pipeline_resumes_without_downloading_again(
    conn, settings, monkeypatch, status
):
    db.upsert_clip(conn, clip_record())
    media = settings.raw_dir / "source.mp4"
    media.write_bytes(b"fixture")
    db.save_media(
        conn,
        "Clip1",
        path=str(media),
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        bytes=7,
        width=640,
        height=360,
        fps=30,
        duration=12,
        has_audio=0,
        method="test",
        permission_ref="",
    )
    db.set_clip_status(conn, "Clip1", status)
    monkeypatch.setattr("clipfactory.pipeline.render_clip", lambda *a, **kw: 123)

    def forbidden_download(*args):
        pytest.fail("must reuse intact recorded media on resume")

    report = run_pipeline(
        conn, settings, cfg(), downloader=forbidden_download, transcriber=NullTranscriber(), limit=1
    )
    assert report.rendered == [123]


def test_non_overlapping_short_clips_are_not_discarded(conn):
    db.upsert_clip(conn, clip_record("A", vod_offset=100, duration=10, score=10))
    db.set_clip_status(conn, "A", ClipStatus.RENDERED)
    db.upsert_clip(conn, clip_record("B", vod_offset=125, duration=10))
    assert [r["clip_id"] for r in select_candidates(conn, cfg(), 1)] == ["B"]


def test_long_clips_with_shared_interval_are_duplicates(conn):
    db.upsert_clip(conn, clip_record("A", vod_offset=100, duration=60))
    db.set_clip_status(conn, "A", ClipStatus.RENDERED)
    db.upsert_clip(conn, clip_record("B", vod_offset=140, duration=60))
    assert select_candidates(conn, cfg(), 1) == []


def test_unprocessed_winner_does_not_permanently_discard_alternative(conn):
    db.upsert_clip(conn, clip_record("A", score=100, vod_offset=100))
    db.upsert_clip(conn, clip_record("B", score=99, vod_offset=105))
    assert [r["clip_id"] for r in select_candidates(conn, cfg(), 5)] == ["A"]
    db.set_clip_status(conn, "A", ClipStatus.FAILED)
    assert [r["clip_id"] for r in select_candidates(conn, cfg(), 5)] == ["B"]


def test_corrupt_transcript_is_regenerated_and_cached_transcript_is_reused(
    conn,
    settings,
    monkeypatch,
):
    from clipfactory.pipeline import load_transcript
    from clipfactory.transcribe import Transcript

    db.upsert_clip(conn, clip_record())
    src = settings.raw_dir / "source.mp4"
    src.write_bytes(b"fixture")
    db.save_media(
        conn,
        "Clip1",
        path=str(src),
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        bytes=7,
        width=640,
        height=360,
        fps=30,
        duration=12,
        has_audio=0,
        method="test",
        permission_ref="",
    )
    db.set_clip_status(conn, "Clip1", ClipStatus.TRANSCRIBED)
    path = settings.transcripts_dir / "Clip1.json"
    path.write_text('{"partial":')
    db.save_transcript(conn, "Clip1", str(path), "none", "", "ru")
    assert load_transcript(conn, "Clip1") is None
    monkeypatch.setattr("clipfactory.pipeline.render_clip", lambda *a, **kw: 1)
    report = run_pipeline(
        conn,
        settings,
        cfg(),
        downloader=lambda *a: pytest.fail("redownload"),
        transcriber=NullTranscriber(),
        limit=1,
    )
    assert report.rendered == [1] and Transcript.load(path).backend == "none"

    class ForbiddenTranscriber:
        def transcribe(self, *a):
            pytest.fail("must reuse successful ASR after interruption")

    report = run_pipeline(
        conn,
        settings,
        cfg(),
        downloader=lambda *a: pytest.fail("redownload"),
        transcriber=ForbiddenTranscriber(),
        limit=1,
    )
    assert report.rendered == [1]


def test_render_probe_timeout_does_not_abort_remaining_candidates(conn, settings, monkeypatch):
    import subprocess

    from clipfactory.fetch import FetchOutcome

    for cid, offset in [("A", 100), ("B", 900)]:
        db.upsert_clip(conn, clip_record(cid, vod_offset=offset))
    monkeypatch.setattr(
        "clipfactory.pipeline.fetch_clip",
        lambda *a: FetchOutcome(a[3]["clip_id"], ClipStatus.FETCHED),
    )
    monkeypatch.setattr("clipfactory.pipeline.transcribe_clip", lambda *a: None)

    def render(conn, settings, cfg, clip):
        if clip["clip_id"] == "A":
            raise subprocess.TimeoutExpired("ffprobe", 60)
        return 99

    monkeypatch.setattr("clipfactory.pipeline.render_clip", render)
    report = run_pipeline(
        conn, settings, cfg(), downloader=lambda *a: None, transcriber=NullTranscriber(), limit=2
    )
    assert report.rendered == [99] and report.not_rendered == ["A"]
    assert db.get_clip(conn, "A")["status"] == ClipStatus.FAILED


def test_permission_config_does_not_claim_rights_verification():
    from clipfactory.doctor import check_streamers

    config = cfg()
    assert config.streamers[0].can_fetch  # compatibility: operator allowlist stays enabled
    assert "not verified" in check_streamers(config)[0].detail


@pytest.mark.parametrize("status", [ClipStatus.FAILED, ClipStatus.RENDERED])
def test_cached_fetch_restores_failed_but_does_not_downgrade_rendered(conn, settings, status):
    from clipfactory.fetch import fetch_clip

    db.upsert_clip(conn, clip_record())
    src = settings.raw_dir / "source.mp4"
    src.write_bytes(b"fixture")
    db.save_media(
        conn,
        "Clip1",
        path=str(src),
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        bytes=7,
        width=640,
        height=360,
        fps=30,
        duration=12,
        has_audio=0,
        method="test",
        permission_ref="",
    )
    db.set_clip_status(conn, "Clip1", status)
    fetch_clip(
        conn, settings, cfg(), db.get_clip(conn, "Clip1"), lambda *a: pytest.fail("must use cache")
    )
    expected = ClipStatus.FETCHED if status == ClipStatus.FAILED else ClipStatus.RENDERED
    assert db.get_clip(conn, "Clip1")["status"] == expected


def test_same_size_corrupt_source_is_not_trusted(conn, settings):
    from clipfactory.fetch import FetchError, fetch_clip

    db.upsert_clip(conn, clip_record())
    src = settings.raw_dir / "source.mp4"
    src.write_bytes(b"broken!")
    db.save_media(
        conn,
        "Clip1",
        path=str(src),
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        bytes=7,
        width=640,
        height=360,
        fps=30,
        duration=12,
        has_audio=0,
        method="test",
        permission_ref="",
    )
    calls = []

    def download(*a):
        calls.append(1)
        raise FetchError("controlled failure")

    result = fetch_clip(conn, settings, cfg(), db.get_clip(conn, "Clip1"), download)
    assert calls == [1] and result.status == ClipStatus.FAILED


def test_discovery_refreshes_delayed_vod_metadata_without_resetting_state(conn):
    db.upsert_clip(conn, clip_record(video_id="", vod_offset=None))
    db.set_clip_status(conn, "Clip1", ClipStatus.FETCHED)
    db.upsert_clip(conn, clip_record(video_id="vod", vod_offset=123, duration=30))
    row = db.get_clip(conn, "Clip1")
    assert row["video_id"] == "vod" and row["vod_offset"] == 123 and row["duration"] == 30
    assert row["status"] == ClipStatus.FETCHED
    db.upsert_clip(conn, clip_record(video_id="", vod_offset=None, duration=0))
    row = db.get_clip(conn, "Clip1")
    assert row["video_id"] == "vod" and row["vod_offset"] == 123 and row["duration"] == 30


@pytest.mark.parametrize("changed", [False, True])
def test_restored_media_only_reuses_transcript_when_content_matches(
    conn, settings, monkeypatch, changed
):
    from clipfactory.media import Probe
    from clipfactory.pipeline import load_transcript
    from clipfactory.transcribe import Segment, Transcript

    db.upsert_clip(conn, clip_record())
    src = settings.raw_dir / "source.mp4"
    old_bytes = b"old-video"
    db.save_media(
        conn, "Clip1", path=str(src), sha256=hashlib.sha256(old_bytes).hexdigest(),
        bytes=len(old_bytes), width=640, height=360, fps=30, duration=12,
        has_audio=1, method="test", permission_ref="",
    )  # fmt: skip
    old = Transcript(segments=[Segment(start=0, end=1, text="old speech")])
    path = old.save(settings.transcripts_dir / "Clip1.json")
    db.save_transcript(conn, "Clip1", str(path), "test", "", "ru")
    db.set_clip_status(conn, "Clip1", ClipStatus.TRANSCRIBED)
    calls = []

    def download(*args):
        src.write_bytes(b"new-video" if changed else old_bytes)
        return src

    class Transcriber:
        def transcribe(self, *args):
            calls.append(1)
            return Transcript(segments=[Segment(start=0, end=1, text="new speech")])

    monkeypatch.setattr(
        "clipfactory.fetch.media.probe",
        lambda *a: Probe(
            width=640, height=360, fps=30, duration=12, has_audio=True, vcodec="h264", acodec="aac"
        ),
    )
    monkeypatch.setattr("clipfactory.pipeline.render_clip", lambda *a: 1)
    report = run_pipeline(
        conn, settings, cfg(), downloader=download, transcriber=Transcriber(), limit=1
    )
    assert report.rendered == [1]
    assert len(calls) == int(changed)
    assert load_transcript(conn, "Clip1").text == ("new speech" if changed else "old speech")
