"""Tests for core.stick (spec 134): the arc/straight-line fit is pure
numpy/cv2 on a luminance image - no pose model or camera needed."""

import cv2
import numpy as np
import pytest

from core.pose import PalmBox
from core.stick import (
    StickArc,
    _curve_points,
    fit_stick_arc,
    hands_from_boxes,
    smoothed_luminance,
)

A = (100.0, 40.0)
B = (100.0, 120.0)


def _image_with_curve(bend_px: float) -> np.ndarray:
    """Mid-grey image with a 3 px wide bright line along the arc A->B."""
    img = np.full((200, 200, 3), 90, dtype=np.uint8)
    pts = np.round(_curve_points(A, B, bend_px, 60)).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(img, [pts], False, (230, 230, 230), 3, cv2.LINE_AA)
    return img


def test_straight_stick_is_reported_straight() -> None:
    arc = fit_stick_arc(smoothed_luminance(_image_with_curve(0.0)), A, B)
    assert arc is not None
    assert not arc.bent
    assert arc.bend_px == 0.0


@pytest.mark.parametrize("bend", [-14.0, 10.0])
def test_bent_stick_is_found_with_the_right_side_and_size(bend: float) -> None:
    arc = fit_stick_arc(smoothed_luminance(_image_with_curve(bend)), A, B)
    assert arc is not None and arc.bent
    assert arc.bend_px == pytest.approx(bend, abs=2.5)


def test_dark_line_on_a_bright_background_is_found_too() -> None:
    img = 255 - _image_with_curve(12.0)
    arc = fit_stick_arc(smoothed_luminance(img), A, B)
    assert arc is not None and arc.bent
    assert arc.bend_px == pytest.approx(12.0, abs=2.5)


def test_no_line_at_all_is_straight() -> None:
    flat = np.full((200, 200, 3), 120, dtype=np.uint8)
    arc = fit_stick_arc(smoothed_luminance(flat), A, B)
    assert arc is not None and not arc.bent


def test_a_wide_edge_is_not_mistaken_for_a_stick() -> None:
    img = np.full((200, 200, 3), 40, dtype=np.uint8)
    img[:, 108:] = 220  # a step edge (like a torso boundary) beside the chord
    arc = fit_stick_arc(smoothed_luminance(img), A, B)
    assert arc is not None and not arc.bent


def test_hands_too_close_give_no_stick() -> None:
    assert fit_stick_arc(np.zeros((50, 50), np.float32), (10.0, 10.0), (12.0, 11.0)) is None


def test_arc_points_start_and_end_at_the_hands_and_bulge_in_the_middle() -> None:
    arc = StickArc(A, B, bend_px=8.0, bent=True)
    pts = arc.points(41)
    assert tuple(pts[0]) == pytest.approx(A) and tuple(pts[-1]) == pytest.approx(B)
    mid = pts[20]
    assert abs(mid[0] - 100.0) == pytest.approx(8.0)  # sideways by bend_px
    assert mid[1] == pytest.approx(80.0)


def test_hands_from_boxes_orders_upper_hand_first_and_needs_both() -> None:
    lower = PalmBox(50.0, 90.0, "right hand")
    upper = PalmBox(60.0, 30.0, "left hand")
    assert hands_from_boxes([lower, upper]) == ((60.0, 30.0), (50.0, 90.0))
    assert hands_from_boxes([upper]) is None
    assert hands_from_boxes([]) is None


# --- the hands sidecar written by the annotation pass ---------------------------


def test_hands_sidecar_path_maps_raw_and_annotated_clips_to_one_file(tmp_path) -> None:
    from core.stick import hands_sidecar_path

    raw = tmp_path / "shot-improvement-20260921103414.mp4"
    annotated = tmp_path / "shot-improvement-20260921103414-annotated.mp4"
    assert hands_sidecar_path(raw) == hands_sidecar_path(annotated) == tmp_path / "shot-improvement-20260921103414-hands.json"


def test_hands_track_round_trip_and_nearest_time_lookup(tmp_path) -> None:
    from core.stick import HandsTrack, write_hands_sidecar

    path = tmp_path / "x-hands.json"
    write_hands_sidecar(path, [0.0, 0.1, 0.2], [((1.0, 2.0), (3.0, 4.0)), None, ((5.0, 6.0), (7.0, 8.0))])
    track = HandsTrack.load(path)
    assert track is not None
    assert track.at(0.02) == ((1.0, 2.0), (3.0, 4.0))
    assert track.at(0.1) is None  # frame without two hands
    assert track.at(0.19) == ((5.0, 6.0), (7.0, 8.0))
    assert track.at(9.0) == ((5.0, 6.0), (7.0, 8.0))  # past the end -> last frame


def test_hands_track_load_returns_none_when_missing_or_corrupt(tmp_path) -> None:
    from core.stick import HandsTrack

    assert HandsTrack.load(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert HandsTrack.load(bad) is None
