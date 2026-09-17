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


def test_detect_claps_keeps_two_events_a_real_gap_apart() -> None:
    """Two distinct events 0.40s apart (the real recording's closest
    genuine pair, per spec 098) must stay separate."""
    audio = _synth([1.00, 1.40], duration_s=3.0)

    claps = detect_claps(audio, SAMPLE_RATE)
    times = [t for t, _ in claps]

    assert len(times) == 2
    assert times[1] - times[0] == pytest.approx(0.40, abs=0.03)


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
    assert pair_claps([]) == ([], None)


def test_pair_claps_single_trailing() -> None:
    assert pair_claps([1.0]) == ([], 1.0)


def test_pair_claps_two_forms_one_pair() -> None:
    assert pair_claps([1.0, 2.0]) == ([(1.0, 2.0)], None)


def test_pair_claps_four_forms_two_pairs() -> None:
    pairs, trailing = pair_claps([1.0, 2.0, 5.0, 6.0])
    assert pairs == [(1.0, 2.0), (5.0, 6.0)]
    assert trailing is None


def test_pair_claps_five_has_trailing_unpaired() -> None:
    pairs, trailing = pair_claps([1.0, 2.0, 5.0, 6.0, 9.0])
    assert pairs == [(1.0, 2.0), (5.0, 6.0)]
    assert trailing == 9.0


def test_puck_speed_kmh_known_value() -> None:
    # 57.0m in 3.0s = 19 m/s = 68.4 km/h
    assert puck_speed_kmh(10.0, 13.0) == pytest.approx(68.4)


def test_puck_speed_kmh_returns_none_for_near_zero_interval() -> None:
    assert puck_speed_kmh(10.0, 10.001) is None


def test_puck_speed_kmh_custom_distance() -> None:
    assert puck_speed_kmh(0.0, 1.0, distance_m=10.0) == pytest.approx(36.0)


def test_real_clip_event_list_pairs_to_plausible_speeds() -> None:
    """Pins the real-world numbers validated during spec 098's planning
    against recordings/shot-improvement-20260915110833-annotated-fixed.mp4
    - a future change to the algorithm shouldn't silently break this
    clip's own known-good result."""
    real_event_times = [6.867, 10.037, 10.472, 14.048, 17.090, 18.077, 21.200, 22.192, 25.275, 26.720]

    pairs, trailing = pair_claps(real_event_times)

    assert trailing is None
    assert len(pairs) == 5
    assert puck_speed_kmh(*pairs[0]) == pytest.approx(64.7, abs=0.5)
    assert puck_speed_kmh(*pairs[1]) == pytest.approx(57.4, abs=0.5)
