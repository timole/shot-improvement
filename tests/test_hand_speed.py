import pytest

from core.hand_speed import estimate_speed


def samples():
    # 200px body = 2m; hand moves at 1000px/s = 10m/s = 36km/h.
    return [{"t": i / 240, "body_px": 200, "right hand": [i / 240 * 1000, 50]}
            for i in range(120, 241)]


def test_speed_uses_elapsed_seconds_and_height_scale():
    assert estimate_speed(samples(), 1, 2, "right hand") == pytest.approx(36)
    assert estimate_speed(samples(), 1, 1, "right hand") == pytest.approx(18)


def test_missing_hand_or_contact_coverage_has_no_speed():
    assert estimate_speed(samples(), 1, 2, "left hand") is None
    assert estimate_speed(samples()[:-8], 1, 2, "right hand") is None


def test_spurious_landmark_jump_is_rejected():
    rows = samples()
    rows[-5]["right hand"] = [1200, 300]
    assert estimate_speed(rows, 1, 2, "right hand") is None
