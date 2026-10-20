from PIL import Image

from clipfactory.calibrate import annotate, box_around_face, calibrate_frame
from clipfactory.config import NormBox

from .conftest import FONT


def test_box_around_face_near_corner_snaps_to_edges():
    # face in the bottom-right webcam of a 1920x1080 frame
    box = box_around_face(1700, 830, 90, 110, 1920, 1080)
    assert box.x + box.w == 1.0 or abs(box.x + box.w - 1.0) < 0.002
    assert box.y + box.h == 1.0 or abs(box.y + box.h - 1.0) < 0.002
    assert 0.2 < box.w < 0.4
    assert box.x * 1920 < 1700 and (box.x + box.w) * 1920 > 1790  # contains the face


def test_box_around_face_is_always_valid():
    for fx, fy in ((0, 0), (900, 500), (1850, 1000)):
        box = box_around_face(fx, fy, 60, 60, 1920, 1080)
        NormBox.model_validate(box.model_dump())  # stays inside the frame


def test_annotate_and_calibrate_frame(tmp_path):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (640, 360), (30, 50, 90)).save(frame)
    out = calibrate_frame(
        frame,
        tmp_path / "grid.png",
        str(FONT),
        box=NormBox(x=0.7, y=0.7, w=0.3, h=0.3),
        detect=False,
    )
    img = Image.open(out.image)
    assert img.size == (640, 360) and out.suggestion is None
    assert img.getpixel((int(0.7 * 640) + 2, int(0.85 * 360)))[0] > 200  # yellow config box edge
    plain = annotate(Image.new("RGB", (100, 100)), str(FONT))
    assert plain.size == (100, 100)
