import numpy as np
from PIL import Image

from clipfactory.hooks import clean_line, format_hook, shorten, suggest_hooks
from clipfactory.textcard import fit_text, hex_to_rgb, render_text_layer
from clipfactory.transcribe import Segment, Transcript, Word

from .conftest import FONT


def _yellow_pixels(img: Image.Image, top: int, bottom: int) -> int:
    arr = np.asarray(img.convert("RGBA"))[top:bottom]
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    return int(((r > 220) & (g > 200) & (b < 80) & (a > 200)).sum())


def test_hex_to_rgb():
    assert hex_to_rgb("#FFE600") == (255, 230, 0)


def test_long_hook_wraps_and_fits_width():
    text = "Electronic: я бы замашнил этот раунд вообще без вопросов, без шансов"
    font, lines, stroke = fit_text(text, str(FONT), 840)
    assert 1 < len(lines) <= 3
    for line in lines:
        left, _, right, _ = font.getbbox(line, stroke_width=stroke)
        assert right - left <= 840


def test_hook_layer_draws_yellow_text_in_upper_third(tmp_path):
    path = render_text_layer(
        "Electronic: я бы замашнил", tmp_path / "hook.png", font_path=str(FONT)
    )
    img = Image.open(path)
    assert img.size == (1080, 1920)
    bbox = img.getbbox()
    assert bbox is not None and bbox[1] >= 250 and bbox[3] <= 700
    assert bbox[0] >= 100 and bbox[2] <= 980  # stays out of TikTok's side margins
    assert _yellow_pixels(img, 250, 700) > 2000


def test_clean_line_and_shorten():
    assert clean_line("— Я бы замашнил.") == "Я бы замашнил"
    assert clean_line("ЭЛЕКТРОНИК ЗАМАШНИЛ 🔥🔥") == "ЭЛЕКТРОНИК ЗАМАШНИЛ"
    assert clean_line("ну и что...") == "ну и что..."
    long = "слово " * 30
    assert len(shorten(long)) <= 61 and shorten(long).endswith("…")
    assert format_hook("Electronic", "я бы замашнил") == "Electronic: я бы замашнил"


def _transcript() -> Transcript:
    return Transcript(
        segments=[
            Segment(start=0.5, end=3.0, text="ну короче смотрим дальше что там"),
            Segment(
                start=6.0,
                end=6.9,
                text="Я бы замашнил!",
                words=[
                    Word(start=6.0, end=6.2, text="Я", prob=0.9),
                    Word(start=6.2, end=6.4, text="бы", prob=0.9),
                    Word(start=6.4, end=6.9, text="замашнил!", prob=0.8),
                ],
            ),
            Segment(start=9.0, end=10.0, text="ага"),
        ]
    )


def test_loud_exclamation_becomes_the_hook():
    envelope = np.full(120, -30.0, dtype=np.float32)
    envelope[60:70] = -12.0  # 6.0-7.0 s is loud
    hooks = suggest_hooks(_transcript(), title="клип", speaker="Electronic", envelope=envelope)
    assert hooks[0].text == "Electronic: Я бы замашнил!"
    assert hooks[0].source == "speech"
    assert (hooks[0].start, hooks[0].end) == (6.0, 6.9)
    assert len(hooks) <= 3
    assert all(h.text != "Electronic: ага" for h in hooks)  # one word is too short


def test_title_is_the_fallback_without_speech():
    hooks = suggest_hooks(None, title="ЭЛЕКТРОНИК ЗАМАШНИЛ 🔥")
    assert [h.text for h in hooks] == ["ЭЛЕКТРОНИК ЗАМАШНИЛ"]
    assert hooks[0].source == "title"


def test_unsure_recognition_is_penalized():
    sure = Transcript(
        segments=[
            Segment(
                start=0,
                end=1,
                text="вот это поворот",
                words=[Word(start=0, end=1, text="вот", prob=0.9)],
            )
        ]
    )
    unsure = Transcript(
        segments=[
            Segment(
                start=0,
                end=1,
                text="вот это поворот",
                words=[Word(start=0, end=1, text="вот", prob=0.2)],
            )
        ]
    )
    assert suggest_hooks(sure)[0].score > suggest_hooks(unsure)[0].score
