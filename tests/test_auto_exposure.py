"""Spec 135: LiveSession.auto_exposure_step's control loop, against a fake
camera whose brightness is proportional to 2**exposure (no hardware)."""

import cv2
import pytest

from core.recorder import MAX_EXPOSURE_DSHOW, MIN_EXPOSURE_DSHOW
from core.session import LiveSession


class FakeCap:
    def __init__(self) -> None:
        self.exposure = -11.0

    def set(self, prop: int, value: float) -> bool:
        if prop == cv2.CAP_PROP_EXPOSURE:
            self.exposure = value
        return True


def _session(cap: FakeCap) -> LiveSession:
    s = LiveSession.__new__(LiveSession)  # no camera/mic/pose model
    s.cap = cap
    s._exposure = cap.exposure
    s._last_luma = None
    s._frames_since_exposure_change = 0
    return s


def _run(scene_gain: float, start: float = -11.0, steps: int = 40) -> tuple[FakeCap, bool]:
    cap = FakeCap()
    cap.exposure = start
    s = _session(cap)
    settled = False
    for _ in range(steps):
        s._frames_since_exposure_change = 5  # enough frames passed
        s._last_luma = min(255.0, scene_gain * 2.0 ** cap.exposure)
        if s.auto_exposure_step():
            settled = True
            break
    return cap, settled


def test_dim_scene_raises_the_exposure_until_the_picture_is_bright_enough() -> None:
    cap, settled = _run(scene_gain=118.0 * 2 ** 8.5, start=-11.0)  # ideal exposure -8.5
    assert settled
    assert cap.exposure == pytest.approx(-8.5, abs=0.4)


def test_bright_scene_lowers_the_exposure() -> None:
    cap, settled = _run(scene_gain=118.0 * 2 ** 10.0, start=-6.0)  # ideal -10
    assert settled
    assert cap.exposure == pytest.approx(-10.0, abs=0.4)


def test_exposure_never_leaves_the_allowed_range() -> None:
    dark, settled_dark = _run(scene_gain=1.0, start=-11.0)  # nearly black: wants far more than allowed
    assert settled_dark and dark.exposure == MAX_EXPOSURE_DSHOW
    glare, settled_glare = _run(scene_gain=1e9, start=-8.0)
    assert settled_glare and glare.exposure == MIN_EXPOSURE_DSHOW


def test_waits_for_fresh_frames_after_a_change() -> None:
    cap = FakeCap()
    s = _session(cap)
    s._last_luma = 20.0
    s._frames_since_exposure_change = 2
    assert s.auto_exposure_step() is False
    assert cap.exposure == -11.0  # untouched: the last change hasn't shown up yet
