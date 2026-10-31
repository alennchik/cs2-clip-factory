from datetime import UTC, datetime, timedelta

import pytest

from clipfactory.config import NormBox
from clipfactory.layout import (
    BOTTOM_H,
    OUT_W,
    TOP_H,
    choose_window,
    cover_box,
    full_foreground_box,
    game_box,
    hook_center_y,
    norm_to_px,
    seam_y,
    webcam_box,
)
from clipfactory.ranking import score_clip


def _even(*values: int) -> bool:
    return all(v % 2 == 0 for v in values)


def test_game_box_for_1080p_source():
    box = game_box(1920, 1080)
    assert (box.w, box.h) == (1014, 1080)
    assert (box.x, box.y) == (452, 0)
    assert abs(box.w / box.h - OUT_W / TOP_H) < 0.003


@pytest.mark.parametrize("center_x", [0.0, 0.25, 0.9, 1.0])
def test_game_box_stays_inside_frame(center_x):
    box = game_box(1920, 1080, center_x)
    assert box.x >= 0 and box.x + box.w <= 1920
    assert _even(box.x, box.y, box.w, box.h)


def test_cover_box_tall_source():
    box = cover_box(720, 1280, OUT_W, TOP_H)
    assert box.w == 720 and box.h <= 1280
    assert box.y >= 0 and box.y + box.h <= 1280


def test_webcam_box_is_inside_the_webcam_area_with_panel_aspect():
    cam = NormBox(x=0.72, y=0.70, w=0.28, h=0.30)
    area = norm_to_px(cam, 1920, 1080)
    box = webcam_box(1920, 1080, cam)
    assert area.x <= box.x and box.x + box.w <= area.x + area.w
    assert area.y <= box.y and box.y + box.h <= area.y + area.h
    assert abs(box.w / box.h - OUT_W / BOTTOM_H) < 0.01
    assert _even(box.x, box.y, box.w, box.h)


def test_tiny_webcam_box_rejected():
    with pytest.raises(ValueError):
        norm_to_px(NormBox(x=0, y=0, w=0.005, h=0.005), 1920, 1080)


def test_full_foreground_is_centered():
    fg = full_foreground_box(1920, 1080)
    assert (fg.w, fg.h) == (1080, 606)
    assert fg.x == 0 and abs((fg.y + fg.h / 2) - 960) <= 2


def test_choose_window():
    assert choose_window(30, 45) == (0.0, 30)
    assert choose_window(60, 45) == (15.0, 45)  # keep the ending
    start, length = choose_window(60, 45, focus=(3.0, 5.0))  # hook said early: move back
    assert start == 1.0 and length == 45


def test_score_prefers_fresh_clips_and_good_lengths():
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    fresh = score_clip(1000, now - timedelta(hours=2), 30, now=now)
    old = score_clip(1000, now - timedelta(hours=40), 30, now=now)
    short = score_clip(1000, now - timedelta(hours=2), 5, now=now)
    weighted = score_clip(1000, now - timedelta(hours=2), 30, now=now, weight=2)
    assert fresh > old
    assert fresh > short
    assert weighted == pytest.approx(fresh * 2, rel=1e-3)


def test_seam_and_middle_hook_position():
    assert seam_y("top") == 770 and seam_y("bottom") == 1150
    assert hook_center_y("split", 1920, 1080) == 770
    assert hook_center_y("split", 1920, 1080, webcam_position="bottom") == 1150
    fg = full_foreground_box(1920, 1080)
    assert hook_center_y("full", 1920, 1080) == fg.y - 110


@pytest.mark.parametrize("w,h", [(360, 800), (720, 1280), (2560, 1080), (640, 480)])
def test_full_fit_preserves_unusual_aspect_ratio(w, h):
    from clipfactory.layout import fit_size

    fitted_w, fitted_h = fit_size(w, h)
    assert fitted_w <= 1080 and fitted_h <= 1920
    assert abs(fitted_w / fitted_h - w / h) < 0.01
