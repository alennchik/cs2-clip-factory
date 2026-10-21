from dataclasses import replace

import numpy as np
from PIL import Image

from clipfactory.config import NormBox, RenderConfig
from clipfactory.media import extract_frame, integrated_loudness, probe
from clipfactory.render import RenderJob, build_filtergraph, caption_chunks, render_video
from clipfactory.transcribe import Segment, Transcript, Word

from .conftest import FONT, WEBCAM, make_clip, needs_ffmpeg

CAM = NormBox(x=WEBCAM[0], y=WEBCAM[1], w=WEBCAM[2], h=WEBCAM[3])
CFG = RenderConfig(font=str(FONT))


def _frame(video, at, tmp_path) -> np.ndarray:
    return np.asarray(Image.open(extract_frame(video, at, tmp_path / f"f{at}.png")).convert("RGB"))


def _yellow(arr: np.ndarray) -> int:
    r, g, b = arr[..., 0].astype(int), arr[..., 1].astype(int), arr[..., 2].astype(int)
    return int(((r > 210) & (g > 190) & (b < 90)).sum())


@needs_ffmpeg
def test_split_render_layout_text_and_loudness(clip_12s, tmp_path):
    out = tmp_path / "split.mp4"
    result = render_video(
        clip_12s, out, cfg=CFG, layout="split", webcam=CAM,
        hook_text="Electronic: я бы замашнил", credit_text="twitch.tv/streamer_a",
        work_dir=tmp_path / "work",
    )  # fmt: skip
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert (info.vcodec, info.acodec) == ("h264", "aac")
    assert abs(info.duration - 12.0) < 0.3
    assert result.layout == "split"  # speed is checked on the Mac, not on shared CI runners

    early = _frame(out, 2.0, tmp_path)
    late = _frame(out, 9.0, tmp_path)
    # webcam panel on top (default): the orange webcam box
    r, g, b = (int(v) for v in early[400, 540])
    assert r > 200 and 100 < g < 190 and b < 70
    # game panel below: dark-blue feed with the white marker centered by the crop
    r, g, b = (int(v) for v in early[1750, 540])
    assert b > r and max(r, g, b) < 130
    assert early[1345, 540].min() > 220
    # hook sits on the seam (y = 770) at the start and is gone after hook_seconds (6 s)
    assert _yellow(early[650:900]) > 2000
    assert _yellow(late[650:900]) < 50
    # the credit (white) takes the seam spot once the hook is gone
    assert (late[792:840, 30:500].min(axis=2) > 220).sum() > 300
    assert (early[792:840, 30:500].min(axis=2) > 220).sum() < 50

    assert abs(integrated_loudness(out) - CFG.loudness_lufs) < 2.0


@needs_ffmpeg
def test_webcam_bottom_and_hook_top_options(clip_12s, tmp_path):
    cfg = CFG.model_copy(update={"webcam_position": "bottom", "hook_position": "top"})
    out = tmp_path / "bottom.mp4"
    render_video(clip_12s, out, cfg=cfg, layout="split", webcam=CAM, hook_text="тест", end=3.0)
    frame = _frame(out, 1.0, tmp_path)
    r, g, b = (int(v) for v in frame[1600, 540])
    assert r > 200 and 100 < g < 190 and b < 70  # webcam below
    assert frame[575, 540].min() > 220  # game marker in the top panel
    assert _yellow(frame[250:500]) > 300  # hook near the top


@needs_ffmpeg
def test_full_layout_when_no_webcam(clip_12s, tmp_path):
    out = tmp_path / "full.mp4"
    result = render_video(clip_12s, out, cfg=CFG, layout="split", webcam=None, hook_text="тест")
    assert result.layout == "full"
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)


@needs_ffmpeg
def test_long_clip_is_trimmed_to_max_duration_keeping_the_end(tmp_path):
    src = make_clip(tmp_path / "long.mp4", duration=50.0, width=1280, height=720)
    result = render_video(src, tmp_path / "trim.mp4", cfg=CFG, layout="split", webcam=CAM)
    assert result.start == 5.0 and result.length == 45.0
    assert abs(probe(result.path).duration - 45.0) < 0.3


@needs_ffmpeg
def test_source_without_audio_gets_a_silent_track(tmp_path):
    src = make_clip(tmp_path / "mute.mp4", duration=4.0, audio=False)
    result = render_video(src, tmp_path / "mute_out.mp4", cfg=CFG, layout="full", webcam=None)
    info = probe(result.path)
    assert info.has_audio and info.acodec == "aac"


@needs_ffmpeg
def test_manual_trim_and_captions(clip_12s, tmp_path):
    transcript = Transcript(segments=[Segment(start=6.0, end=7.0, text="я бы замашнил", words=[
        Word(start=6.0, end=6.3, text="я"), Word(start=6.3, end=6.5, text="бы"),
        Word(start=6.5, end=7.0, text="замашнил")])])  # fmt: skip
    cfg = CFG.model_copy(update={"captions": True})
    result = render_video(
        clip_12s, tmp_path / "cap.mp4", cfg=cfg, layout="split", webcam=CAM,
        transcript=transcript, start=4.0, end=10.0,
    )  # fmt: skip
    assert result.start == 4.0 and result.length == 6.0
    frame = _frame(result.path, 2.6, tmp_path)  # 6.6 s in the source: caption on screen
    white = (frame[600:760].min(axis=2) > 225).sum()  # just above the seam
    assert white > 1500


def test_caption_chunks_are_relative_to_the_window():
    transcript = Transcript(segments=[Segment(start=0, end=3, text="", words=[
        Word(start=0.0, end=0.4, text="раз"), Word(start=0.4, end=0.8, text="два"),
        Word(start=0.8, end=1.2, text="три"), Word(start=1.2, end=1.6, text="четыре"),
        Word(start=5.0, end=5.5, text="пять")])])  # fmt: skip
    chunks = caption_chunks(transcript, start=0.4, length=5.0)
    assert chunks[0] == ("два три четыре", 0.0, 1.2)
    assert chunks[-1][0] == "пять" and chunks[-1][1] == 4.6


def test_filtergraph_maps_audio_only_when_present(clip_12s):
    info = probe(clip_12s)
    job = RenderJob(src=clip_12s, out=clip_12s, probe=info, layout="split", webcam=CAM)
    graph = build_filtergraph(job)
    assert "vstack=inputs=2" in graph and "loudnorm=I=-14" in graph
    silent = RenderJob(
        src=clip_12s, out=clip_12s, probe=replace(info, has_audio=False), layout="full"
    )
    assert "loudnorm" not in build_filtergraph(silent)
