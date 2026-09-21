"""Pure-logic tests for core.claps (spec 098) - no cv2, no real audio
files, matching tests/test_cloud_sync.py's style. See core/claps.py's
module docstring for why each constant has the value it does."""

import numpy as np
import pytest

from core.claps import (
    ABSOLUTE_FLOOR_RMS,
    STARTUP_SKIP_S,
    detect_claps,
    pair_claps,
    puck_speed_kmh,
)

SAMPLE_RATE = 44100


def _synth(events: list[float], duration_s: float, noise_rms: float = 100.0, sample_rate: int = SAMPLE_RATE, burst_rms: float = 8000.0, burst_ms: float = 15.0) -> np.ndarray:
    """Gaussian noise floor + short decaying int16-range bursts at the
    given times - a stand-in for a real recording's ambient noise +
    percussive transients."""
    rng = np.random.default_rng(0)
    n = int(duration_s * sample_rate)
    audio = rng.normal(0.0, noise_rms, n)
    burst_len = int(burst_ms / 1000 * sample_rate)
    decay = np.exp(-np.linspace(0, 6, burst_len))
    for t in events:
        start = int(t * sample_rate)
        end = min(start + burst_len, n)
        audio[start:end] += burst_rms * decay[: end - start] * rng.choice([-1, 1], end - start)
    return np.clip(audio, -32768, 32767).astype(np.int16)


def test_detect_claps_finds_injected_events_within_tolerance() -> None:
    audio = _synth([1.0, 2.0, 3.5], duration_s=5.0)

    claps = detect_claps(audio, SAMPLE_RATE)
    times = [t for t, _ in claps]

    assert len(times) == 3
    for expected, actual in zip([1.0, 2.0, 3.5], times):
        assert actual == pytest.approx(expected, abs=0.03)


def test_detect_claps_ignores_startup_pop() -> None:
    """A loud burst inside STARTUP_SKIP_S is a known mic-startup
    artifact (spec 097), not a real event - must not be detected."""
    audio = _synth([0.05, 1.0, 2.0], duration_s=4.0, burst_rms=15000.0)

    claps = detect_claps(audio, SAMPLE_RATE)
    times = [t for t, _ in claps]

    assert all(t >= STARTUP_SKIP_S for t in times)
    assert len(times) == 2
    assert times[0] == pytest.approx(1.0, abs=0.03)
    assert times[1] == pytest.approx(2.0, abs=0.03)


def test_detect_claps_returns_nothing_for_digital_silence() -> None:
    audio = np.zeros(SAMPLE_RATE * 3, dtype=np.int16)

    assert detect_claps(audio, SAMPLE_RATE) == []


def test_detect_claps_returns_nothing_for_near_silence() -> None:
    """ABSOLUTE_FLOOR_RMS must stop a near-silent clip's adaptive
    threshold collapsing toward ~0 and firing on rounding noise."""
    rng = np.random.default_rng(1)
    audio = rng.normal(0.0, 5.0, SAMPLE_RATE * 3).astype(np.int16)

    assert detect_claps(audio, SAMPLE_RATE) == []


def test_detect_claps_merges_a_decay_tail_into_one_event() -> None:
    """Two bursts 0.10s apart (well under MIN_SEPARATION_S) must be
    treated as one event, not two."""
    audio = _synth([1.00, 1.10], duration_s=3.0)

    claps = detect_claps(audio, SAMPLE_RATE)

    assert len(claps) == 1


def test_detect_claps_merges_a_close_shot_like_pair_too() -> None:
    """Spec 111: two events 0.40s apart - both loud and spectrally
    shot-like (a real stick "windup" tap immediately before its own
    shot, measured 0.36-0.43s apart on a real clip) - are ALSO merged
    into one, now that MIN_SEPARATION_S was widened from spec 098's
    0.25s to 0.5s specifically for this case. Only the louder of the
    two survives."""
    audio = _synth([1.00, 1.40], duration_s=3.0)

    claps = detect_claps(audio, SAMPLE_RATE)

    assert len(claps) == 1


def test_detect_claps_keeps_two_events_a_real_gap_apart() -> None:
    """Two distinct events comfortably past MIN_SEPARATION_S (0.5s)
    must stay separate."""
    audio = _synth([1.00, 1.70], duration_s=3.0)

    claps = detect_claps(audio, SAMPLE_RATE)
    times = [t for t, _ in claps]

    assert len(times) == 2
    assert times[1] - times[0] == pytest.approx(0.70, abs=0.03)


def test_detect_claps_rejects_wall_of_noise() -> None:
    """Uniformly loud noise (no real transient structure) must return
    nothing rather than hundreds of overlapping false events."""
    rng = np.random.default_rng(2)
    audio = (rng.normal(0.0, 6000.0, SAMPLE_RATE * 3)).astype(np.int16)

    assert detect_claps(audio, SAMPLE_RATE) == []


def test_detect_claps_handles_stereo_input() -> None:
    mono = _synth([1.0], duration_s=2.0)
    stereo = np.stack([mono, mono], axis=1)

    claps = detect_claps(stereo, SAMPLE_RATE)

    assert len(claps) == 1
    assert claps[0].time_s == pytest.approx(1.0, abs=0.03)


def test_detect_claps_handles_empty_and_short_input() -> None:
    assert detect_claps(np.array([], dtype=np.int16), SAMPLE_RATE) == []
    assert detect_claps(np.zeros(10, dtype=np.int16), SAMPLE_RATE) == []


def test_pair_claps_empty() -> None:
    assert pair_claps([]) == []


def test_pair_claps_single_shot_has_no_hit() -> None:
    assert pair_claps([1.0]) == [(1.0, None)]


def test_pair_claps_two_within_window_forms_one_pair() -> None:
    # gap = 2.5s, within [MIN_HIT_DELAY_S, MAX_HIT_DELAY_S] (1.5-4.0s)
    assert pair_claps([1.0, 3.5]) == [(1.0, 3.5)]


def test_pair_claps_four_forms_two_pairs() -> None:
    assert pair_claps([1.0, 3.5, 10.0, 12.5]) == [(1.0, 3.5), (10.0, 12.5)]


def test_pair_claps_trailing_shot_with_no_hit() -> None:
    assert pair_claps([1.0, 3.5, 10.0]) == [(1.0, 3.5), (10.0, None)]


def test_pair_claps_gap_too_short_leaves_both_unpaired() -> None:
    """0.8s is too soon to be a real hit (spec 111's MIN_HIT_DELAY_S) -
    the first clap gets no hit, and the second one starts its own
    (also ultimately unpaired) shot rather than being silently
    discarded."""
    assert pair_claps([1.0, 1.8]) == [(1.0, None), (1.8, None)]


def test_pair_claps_gap_too_long_leaves_both_unpaired() -> None:
    """5.0s exceeds MAX_HIT_DELAY_S - too long to be this shot's hit."""
    assert pair_claps([1.0, 6.0]) == [(1.0, None), (6.0, None)]


def test_pair_claps_unpaired_shot_can_be_in_the_middle_not_just_trailing() -> None:
    """Spec 111: unlike spec 098's original "only the very last clap
    can be unpaired" behavior, a shot with no plausible hit anywhere in
    the clip is left unpaired right where it happens."""
    shots = pair_claps([1.0, 3.5, 10.0, 20.0, 22.3])

    assert shots == [(1.0, 3.5), (10.0, None), (20.0, 22.3)]


def test_puck_speed_kmh_known_value() -> None:
    # 57.0m in 3.0s = 19 m/s = 68.4 km/h
    assert puck_speed_kmh(10.0, 13.0) == pytest.approx(68.4)


def test_puck_speed_kmh_returns_none_for_near_zero_interval() -> None:
    assert puck_speed_kmh(10.0, 10.001) is None


def test_puck_speed_kmh_custom_distance() -> None:
    assert puck_speed_kmh(0.0, 1.0, distance_m=10.0) == pytest.approx(36.0)


def test_real_clip_event_list_pairs_to_plausible_speeds() -> None:
    """Pins the real-world numbers from two independently-validated real
    recordings against a future algorithm change:
    recordings/shot-improvement-20260915110833-annotated-fixed.mp4
    (spec 098's own clip) and this session's own
    shot-improvement-20260918133853.mp4 (spec 111, with real
    user-marked ground truth: 6 shots, only 2 with an audible hit).

    Spec 111's windowed pairing (replacing spec 098's naive "pair every
    two consecutive claps") also fixes what spec 098's own docstring
    called an "accepted limitation": the old index-based pairing forced
    17.090 and 26.720 into implausible ~140-208 km/h pairs; the new
    pairing correctly leaves both unpaired (no clap lands in a
    physically plausible hit window after either) and finds two
    MORE genuinely plausible pairs instead."""
    real_event_times = [6.867, 10.037, 10.472, 14.048, 17.090, 18.077, 21.200, 22.192, 25.275, 26.720]

    shots = pair_claps(real_event_times)
    pairs = [(shot_t, hit_t) for shot_t, hit_t in shots if hit_t is not None]
    unpaired = [shot_t for shot_t, hit_t in shots if hit_t is None]

    assert unpaired == [17.090, 26.720]
    assert len(pairs) == 4
    assert puck_speed_kmh(*pairs[0]) == pytest.approx(64.7, abs=0.5)
    assert puck_speed_kmh(*pairs[1]) == pytest.approx(57.4, abs=0.5)
    assert puck_speed_kmh(*pairs[2]) == pytest.approx(65.7, abs=0.5)
    assert puck_speed_kmh(*pairs[3]) == pytest.approx(66.6, abs=0.5)


def test_real_clip_two_ground_truth_pairs_and_four_unpaired_shots() -> None:
    """This session's own recording (spec 111), real Audacity-marked
    ground truth: 6 shots at 3.238/7.365/11.302/15.429/19.556/23.714s,
    only the first two with an audible hit (6.191s, 10.191s). The
    detector's own real output on this clip's audio lands a few tens of
    ms from those hand marks (RMS-envelope granularity, ~5.8ms windows,
    vs. a human eyeballing a waveform) - this test pins the DETECTOR's
    own real event list (not the hand marks) straight into pair_claps,
    so a future change can't silently drop back to 2 shots or invent a
    hit for one of the 4 that has none."""
    detected_event_times = [3.216, 6.124, 7.338, 10.182, 11.326, 15.476, 19.551, 23.719]

    shots = pair_claps(detected_event_times)
    pairs = [(shot_t, hit_t) for shot_t, hit_t in shots if hit_t is not None]
    unpaired = [shot_t for shot_t, hit_t in shots if hit_t is None]

    assert len(pairs) == 2
    assert unpaired == [11.326, 15.476, 19.551, 23.719]
    assert puck_speed_kmh(*pairs[0]) == pytest.approx(70.6, abs=0.5)
    assert puck_speed_kmh(*pairs[1]) == pytest.approx(72.1, abs=0.5)


# --- spec 128: shot positions / distance-scaled pairing -----------------------


def test_shot_positions_distances_and_default() -> None:
    from core.claps import DEFAULT_SHOT_POSITION, SHOT_POSITIONS

    by_key = {p.key: p.distance_m for p in SHOT_POSITIONS}
    assert by_key == {
        "blue_line": pytest.approx(18.5),
        "blue_line_goal_back": pytest.approx(19.62),
        "blue_line_to_end": pytest.approx(22.5),
        "attack_dots": pytest.approx(6.0),
        "red_line": pytest.approx(26.0),
        "other_blue_line": pytest.approx(33.5),
        "faceoff_dots": pytest.approx(46.0),
        "end_to_end": pytest.approx(57.0),
    }
    assert DEFAULT_SHOT_POSITION.key == "blue_line"


def test_hit_delay_window_scales_with_distance_and_keeps_the_original_for_57m() -> None:
    from core.claps import PUCK_TRAVEL_DISTANCE_M, hit_delay_window

    assert hit_delay_window(PUCK_TRAVEL_DISTANCE_M) == pytest.approx((1.5, 4.0))
    lo, hi = hit_delay_window(18.5)
    assert lo == pytest.approx(0.487, abs=0.01)
    assert hi == pytest.approx(1.298, abs=0.01)


def test_pair_claps_accepts_a_short_blue_line_shot_only_with_its_distance() -> None:
    # 0.8 s apart: a 83 km/h shot over 18.5 m, impossible over 57 m.
    assert pair_claps([1.0, 1.8], distance_m=18.5) == [(1.0, 1.8)]
    assert pair_claps([1.0, 1.8]) == [(1.0, None), (1.8, None)]
