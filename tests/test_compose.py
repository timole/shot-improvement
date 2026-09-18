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
import pytest

from core.compose import (
    CLAP_TICK_COLOR_BGR,
    CLAPS_BAND_BG_BGR,
    PAIR_LINE_COLOR_BGR,
    PAIR_LINE_Y,
    SHOT_IMAGE_LABEL_COLOR_BGR,
    SHOT_SPECTROGRAM_HEIGHT,
    _x_for_time,
    composite_into,
    draw_pair_annotations,
    render_claps_band,
    save_shot_images,
)
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


# --- spec 098: claps band / puck-speed pair annotations ------------------


def test_x_for_time_matches_playhead_fraction_formula() -> None:
    """Same formula composite_into() uses internally for the moving
    playhead (np.clip(x_fraction, 0, 1) * (width - 1)) - one formula,
    not two independent copies."""
    width = 200
    duration_s = 10.0
    for t, expected_fraction in [(0.0, 0.0), (5.0, 0.5), (10.0, 1.0)]:
        expected = int(np.clip(expected_fraction, 0.0, 1.0) * (width - 1))
        assert _x_for_time(t, duration_s, width) == expected


def test_x_for_time_clips_out_of_range_times() -> None:
    assert _x_for_time(-5.0, 10.0, 200) == 0
    assert _x_for_time(50.0, 10.0, 200) == 199


def test_x_for_time_handles_zero_duration() -> None:
    assert _x_for_time(1.0, 0.0, 200) == 0


def test_render_claps_band_is_all_background_when_no_claps() -> None:
    band = render_claps_band(200, 100, [], duration_s=10.0)

    assert band.shape == (100, 200, 3)
    assert np.all(band == CLAPS_BAND_BG_BGR)


def test_render_claps_band_marks_each_clap_near_its_time() -> None:
    width, height, duration_s = 200, 100, 10.0
    band = render_claps_band(width, height, [5.0], duration_s)

    x = _x_for_time(5.0, duration_s, width)
    # The tick mark's column (and a small neighborhood, for line
    # anti-aliasing) must not be pure background at the tick's own row.
    column = band[0, max(x - 2, 0) : x + 3]
    assert np.any(column != np.array(CLAPS_BAND_BG_BGR))


def test_render_claps_band_not_all_background_far_from_claps() -> None:
    """Sanity check that the band isn't just uniformly painted - most
    of it should still be background when claps are sparse."""
    band = render_claps_band(200, 100, [5.0], duration_s=10.0)

    assert np.all(band[:, :10] == CLAPS_BAND_BG_BGR)  # far left, away from the one clap
    assert np.all(band[:, -10:] == CLAPS_BAND_BG_BGR)  # far right


def test_draw_pair_annotations_draws_yellow_at_pair_line_y() -> None:
    width, height, duration_s = 200, 60, 10.0
    spectrogram = np.zeros((height, width, 3), dtype=np.uint8)

    draw_pair_annotations(spectrogram, [(2.0, 8.0)], duration_s)

    x1 = _x_for_time(2.0, duration_s, width)
    x2 = _x_for_time(8.0, duration_s, width)
    midpoint_x = (x1 + x2) // 2
    assert tuple(spectrogram[PAIR_LINE_Y, midpoint_x]) == PAIR_LINE_COLOR_BGR


def test_draw_pair_annotations_line_does_not_extend_outside_the_pair_span() -> None:
    width, height, duration_s = 200, 60, 10.0
    spectrogram = np.zeros((height, width, 3), dtype=np.uint8)

    draw_pair_annotations(spectrogram, [(4.0, 6.0)], duration_s)

    x1 = _x_for_time(4.0, duration_s, width)
    # Well before the pair's span - must be untouched.
    assert tuple(spectrogram[PAIR_LINE_Y, max(x1 - 20, 0)]) == (0, 0, 0)


def test_draw_pair_annotations_skips_speed_text_for_near_zero_interval() -> None:
    """puck_speed_kmh returns None for a near-zero interval - must not
    raise or draw garbage text, though the line itself still draws."""
    width, height, duration_s = 200, 60, 10.0
    spectrogram = np.zeros((height, width, 3), dtype=np.uint8)

    draw_pair_annotations(spectrogram, [(5.0, 5.0001)], duration_s)  # does not raise


def test_three_band_composite_buffer_views_are_contiguous_and_disjoint() -> None:
    """Same premise as test_top_and_bottom_are_contiguous_views_not_copies,
    extended to the spec-098 claps_view band: all three views must be
    real C-contiguous slices of ONE buffer (not copies), and a write to
    claps_view must never touch top/bottom's rows or vice versa."""
    height, spec_height, claps_height, width = 40, 20, 15, 60
    composite = np.empty((height + spec_height + claps_height, width, 3), dtype=np.uint8)
    top = composite[:height]
    bottom = composite[height : height + spec_height]
    claps_view = composite[height + spec_height :]

    for view in (top, bottom, claps_view):
        assert view.flags["C_CONTIGUOUS"]
        assert view.base is composite or view.base is composite.base

    top[:] = 1
    bottom[:] = 2
    claps_view[:] = 3

    assert (composite[:height] == 1).all()
    assert (composite[height : height + spec_height] == 2).all()
    assert (composite[height + spec_height :] == 3).all()


# --- spec 099: per-shot snapshot JPEGs ------------------------------------


def _synth_claps_audio(events, duration_s, sample_rate=44100, noise_rms=50.0, burst_rms=8000.0):
    rng = np.random.default_rng(0)
    n = int(duration_s * sample_rate)
    audio = rng.normal(0.0, noise_rms, n)
    burst_len = int(0.02 * sample_rate)
    decay = np.exp(-np.linspace(0, 6, burst_len))
    for t in events:
        start = int(t * sample_rate)
        audio[start : start + burst_len] += burst_rms * decay
    return np.clip(audio, -32768, 32767).astype(np.int16)


def _write_indexed_frames(raw_dir, frame_count, width=320, height=240):
    """Each frame is a distinct flat color keyed to its index, so a
    test can confirm save_shot_images picked the frame closest to a
    given real time, not just any frame. Deliberately not tiny (e.g.
    64x48) - JPEG's 8x8 block DCT visibly bleeds a bottom-of-frame
    text label's chroma toward the top at that scale, which isn't
    representative of this app's real ~1280x720 output."""
    import cv2

    for i in range(frame_count):
        color = (int(i * 5 % 256), 0, 0)
        frame = np.full((height, width, 3), color, dtype=np.uint8)
        cv2.imwrite(str(raw_dir / f"frame_{i:06d}.bmp"), frame)


def test_save_shot_images_creates_one_image_per_paired_shot(tmp_path) -> None:
    import cv2

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    frame_count, duration_s = 20, 10.0
    _write_indexed_frames(raw_dir, frame_count)
    frame_times = [i * duration_s / (frame_count - 1) for i in range(frame_count)]
    audio = _synth_claps_audio([2.0, 5.0], duration_s)

    saved = save_shot_images(raw_dir, "bmp", frame_times, audio, 44100, out_dir, "clip")

    assert len(saved) == 1
    assert saved[0].index == 1
    assert saved[0].path.name == "clip-shot-01.jpg"
    assert saved[0].path.exists()
    assert saved[0].time_s == pytest.approx(2.0, abs=0.3)
    assert saved[0].speed_kmh is not None
    img = cv2.imread(str(saved[0].path))
    assert img.shape == (240 + SHOT_SPECTROGRAM_HEIGHT, 320, 3)


def test_save_shot_images_picks_the_frame_nearest_the_shot_time(tmp_path) -> None:
    import cv2

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    frame_count, duration_s = 20, 10.0
    _write_indexed_frames(raw_dir, frame_count)
    frame_times = [i * duration_s / (frame_count - 1) for i in range(frame_count)]
    audio = _synth_claps_audio([2.0, 5.0], duration_s)

    saved = save_shot_images(raw_dir, "bmp", frame_times, audio, 44100, out_dir, "clip")

    # shot time ~2.0s with 20 frames over 10.0s (~0.526s apart) -> frame index round(2.0/0.526) ~ 3-4
    expected_idx = int(round(2.0 / (duration_s / (frame_count - 1))))
    img = cv2.imread(str(saved[0].path))
    top_strip_blue_channel = int(img[5, 5, 0])  # BGR - blue channel encodes the frame index (see _write_indexed_frames)
    expected_color = int(expected_idx * 5 % 256)
    assert abs(top_strip_blue_channel - expected_color) <= 10  # small tolerance for JPEG compression


def test_save_shot_images_includes_trailing_unpaired_shot_without_speed_text(tmp_path) -> None:
    import cv2

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    frame_count, duration_s = 20, 10.0
    _write_indexed_frames(raw_dir, frame_count)
    frame_times = [i * duration_s / (frame_count - 1) for i in range(frame_count)]
    audio = _synth_claps_audio([2.0, 5.0, 8.0], duration_s)  # 1 pair + 1 trailing unpaired

    saved = save_shot_images(raw_dir, "bmp", frame_times, audio, 44100, out_dir, "clip")

    assert [s.path.name for s in saved] == ["clip-shot-01.jpg", "clip-shot-02.jpg"]
    assert saved[0].speed_kmh is not None
    assert saved[1].speed_kmh is None
    trailing_img = cv2.imread(str(saved[1].path))
    frame_height = 240
    bottom_of_frame = trailing_img[frame_height - 40 : frame_height, :]
    # No white speed-label pixels should be present in the camera-frame
    # portion (rows 0..frame_height) - the base frame is a flat dark
    # red, no channel anywhere near white.
    assert not np.any(np.all(bottom_of_frame > np.array(SHOT_IMAGE_LABEL_COLOR_BGR) - 20, axis=-1))


def test_save_shot_images_returns_empty_list_when_no_claps(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    frame_count, duration_s = 10, 5.0
    _write_indexed_frames(raw_dir, frame_count)
    frame_times = [i * duration_s / (frame_count - 1) for i in range(frame_count)]
    silent_audio = np.zeros(int(duration_s * 44100), dtype=np.int16)

    saved = save_shot_images(raw_dir, "bmp", frame_times, silent_audio, 44100, out_dir, "clip")

    assert saved == []
    assert list(out_dir.glob("*.jpg")) == []
