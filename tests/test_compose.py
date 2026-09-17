"""Proves core.compose.composite_into() is pixel-identical to the old
two-pass pipeline it replaces (spec 092): draw_palm_boxes()+frame.copy()
(core.pose.annotate_frames_dir) followed by np.vstack([frame,
with_playhead(spectrogram, x)]) (core.spectrogram.
add_spectrograms_to_frames). Both reference functions are kept in
place specifically to serve as this oracle - see their modules'
docstrings.

No camera, no PoseDetector, no disk - pure array comparison,
milliseconds."""

import numpy as np

from core.compose import composite_into
from core.pose import PalmBox, draw_palm_boxes
from core.spectrogram import with_playhead


def _reference_composite(frame: np.ndarray, boxes, spectrogram: np.ndarray, x_fraction: float) -> np.ndarray:
    """What the old two-pass pipeline produced, byte for byte:
    annotate_frames_dir drew onto a COPY of the frame and wrote it;
    add_spectrograms_to_frames read that back and stacked a
    with_playhead panel underneath."""
    annotated = frame.copy()
    draw_palm_boxes(annotated, boxes)
    panel = with_playhead(spectrogram, x_fraction)
    return np.vstack([annotated, panel])


def _random_frame(rng: np.random.Generator, height: int, width: int) -> np.ndarray:
    return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)


def _random_spectrogram(rng: np.random.Generator, height: int, width: int) -> np.ndarray:
    return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)


def _new_composite_buffer(height: int, spectrogram_height: int, width: int):
    composite = np.empty((height + spectrogram_height, width, 3), dtype=np.uint8)
    return composite, composite[:height], composite[height:]


def test_composite_into_matches_reference_pipeline_for_several_x_fractions() -> None:
    rng = np.random.default_rng(0)
    height, width, spec_height = 64, 96, 32
    boxes = [PalmBox(cx=20.0, cy=30.0, label="vasen kasi"), PalmBox(cx=70.0, cy=40.0, label="oikea kasi")]
    spectrogram = _random_spectrogram(rng, spec_height, width)

    for x_fraction in (0.0, 0.25, 0.5, 0.75, 1.0, -1.0, 2.0):
        frame = _random_frame(rng, height, width)
        expected = _reference_composite(frame.copy(), boxes, spectrogram, x_fraction)

        composite, top, bottom = _new_composite_buffer(height, spec_height, width)
        composite_into(top, bottom, frame, boxes, spectrogram, x_fraction)

        assert np.array_equal(composite, expected), f"mismatch at x_fraction={x_fraction}"


def test_composite_into_matches_reference_with_no_boxes() -> None:
    rng = np.random.default_rng(1)
    height, width, spec_height = 48, 80, 24
    spectrogram = _random_spectrogram(rng, spec_height, width)
    frame = _random_frame(rng, height, width)

    expected = _reference_composite(frame.copy(), [], spectrogram, 0.4)

    composite, top, bottom = _new_composite_buffer(height, spec_height, width)
    composite_into(top, bottom, frame, [], spectrogram, 0.4)

    assert np.array_equal(composite, expected)


def test_top_and_bottom_are_contiguous_views_not_copies() -> None:
    """The whole point of the preallocated buffer (spec 092) depends on
    this: if top/bottom were non-contiguous, cv2 functions would
    silently operate on a temporary copy and composite_into's writes
    would never reach `composite`."""
    composite, top, bottom = _new_composite_buffer(40, 20, 60)
    assert top.flags["C_CONTIGUOUS"]
    assert bottom.flags["C_CONTIGUOUS"]
    assert top.base is composite or top.base is composite.base
    assert bottom.base is composite or bottom.base is composite.base

    top[:] = 7
    assert (composite[:40] == 7).all()


def test_bmp_round_trip_is_lossless() -> None:
    """Load-bearing premise of the whole fusion (spec 092): the old
    pipeline's annotate->write->read->composite round trip through a
    BMP file must be byte-identical to keeping the array in memory, or
    dropping that round trip would change output."""
    import tempfile
    from pathlib import Path

    import cv2

    rng = np.random.default_rng(2)
    frame = _random_frame(rng, 64, 96)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "frame.bmp"
        cv2.imwrite(str(path), frame)
        reloaded = cv2.imread(str(path))
        assert np.array_equal(frame, reloaded)
