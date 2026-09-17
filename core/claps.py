"""Detects loud percussive audio events ("claps") in a clip - a puck
leaving the stick ("shot") or hitting the boards at the far end of the
rink ("hit") - and turns consecutive pairs into a measured puck speed.
See spec 098.

v1, deliberately approximate. The detection algorithm and every tuning
constant below were validated against a real 30s recording
(recordings/shot-improvement-20260915110833-annotated-fixed.mp4, in the
scratchpad during planning, not guessed): a naive threshold (2.2x the
clip's median RMS) produced 24-40 false positives from ambient rink
noise; 5.0x cleanly finds exactly the real events with zero false
positives. A high-pass ("np.diff") pre-emphasis pass was also tried and
rejected - it separated the loud "hit" tier from noise very well but
pushed the quieter "shot" tier below the detection floor entirely,
which is worse overall.

Pairing is simple chronological order (clap 0 & 1 = pair 1, 2 & 3 =
pair 2, ...), not amplitude-based classification - amplitude looked
like a more robust discriminator on paper, but the real clip's first
shot (RMS ~10500) sits in the exact same loud tier as every "hit"
event, so classifying by loudness would misclassify it. Chronological
pairing, given an accurate detector, matched the user's own estimate
for this clip almost exactly (measured 64.7/57.4 km/h against their
own "~60 km/h, ~3 seconds").

Known, accepted limitations (not bugs): the fixed 57m shot-to-boards
distance doesn't hold for every shot in a practice session (a
shorter-range drill shot paired chronologically will show an
unrealistic speed - this is expected v1 output, not something this
module tries to detect/correct); a shot too quiet to clear the
detection threshold is simply invisible (raising the threshold to
catch it reintroduces false positives elsewhere, so this module
doesn't try).
"""

from __future__ import annotations

from typing import NamedTuple, Optional

import numpy as np

from .log_setup import get_logger

logger = get_logger("claps")

# A regulation hockey rink is 61m end to end; a shot taken from the goal
# line (4m in from the end being shot at) therefore travels 57m to reach
# the far boards.
RINK_LENGTH_M = 61.0
GOAL_LINE_OFFSET_M = 4.0
PUCK_TRAVEL_DISTANCE_M = RINK_LENGTH_M - GOAL_LINE_OFFSET_M  # 57.0

RMS_WINDOW = 256  # ~5.8ms @ 44.1kHz
# Known mic-startup pop artifact (diagnosed in spec 097's AV-sync work) -
# excluded from both detection and the threshold's own statistics, since
# it's loud enough to otherwise skew the median.
STARTUP_SKIP_S = 0.3
# Tuned empirically (see module docstring) - how many times the clip's
# own median RMS counts as a real event. Deliberately per-clip-adaptive
# rather than a fixed absolute number: ambient noise level varies hugely
# between a quiet room (median ~60-140, see spec 097's diagnostic test)
# and a noisy rink (median ~500).
THRESHOLD_MEDIAN_RATIO = 5.0
# Independent floor so a near-silent clip's threshold can't collapse to
# ~0 and fire on rounding noise.
ABSOLUTE_FLOOR_RMS = 500.0
# Above-threshold samples within this many seconds of each other are
# treated as ONE event (its timestamp is the loudest sample in that
# whole span) - not a fixed tiny window-gap, and not a separate
# "earliest wins" merge pass after clustering (tried, and wrong: it can
# suppress a real loud peak in favor of a weaker spurious blip that
# happened to cross threshold slightly earlier).
MIN_SEPARATION_S = 0.25
# Guards a pathological wall-of-noise clip - if detected events would be
# denser than this, something is wrong with the input, not the clip.
MAX_CLAPS_PER_SECOND = 2.0
# Guards puck_speed_kmh's division - shouldn't be reachable given
# MIN_SEPARATION_S, but cheap to guard explicitly.
MIN_PAIR_INTERVAL_S = 0.05


class Clap(NamedTuple):
    time_s: float
    peak_rms: float


def rms_envelope(audio: np.ndarray, sample_rate: int, window: int = RMS_WINDOW) -> tuple[np.ndarray, np.ndarray]:
    """Per-window RMS energy and each window's start time - vectorized,
    same mono-flattening convention as core.spectrogram.compute_spectrogram_image."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float64)
    n_windows = len(audio) // window
    if n_windows == 0:
        return np.array([]), np.array([])
    trimmed = audio[: n_windows * window].reshape(n_windows, window)
    rms = np.sqrt(np.mean(trimmed ** 2, axis=1))
    times = np.arange(n_windows) * window / sample_rate
    return rms, times


def detect_claps(audio: np.ndarray, sample_rate: int) -> list[Clap]:
    """Returns each detected event's (time_s, peak_rms), sorted by time.
    See module docstring for the algorithm and why each constant has
    the value it does."""
    rms, times = rms_envelope(audio, sample_rate)
    if len(rms) == 0:
        return []

    valid = times >= STARTUP_SKIP_S
    stats_rms = rms[valid]
    if len(stats_rms) == 0:
        return []
    median = float(np.median(stats_rms))
    threshold = max(ABSOLUTE_FLOOR_RMS, median * THRESHOLD_MEDIAN_RATIO)

    above = valid & (rms > threshold)
    idx = np.where(above)[0]
    if len(idx) == 0:
        logger.info("detect_claps: no events above threshold=%.1f (median=%.1f)", threshold, median)
        return []

    # Cluster by TIME gap directly: any run of above-threshold samples
    # where consecutive ones are within MIN_SEPARATION_S of each other
    # is one event.
    clusters: list[tuple[int, int]] = []
    start = idx[0]
    prev = idx[0]
    for j in idx[1:]:
        if times[j] - times[prev] > MIN_SEPARATION_S:
            clusters.append((start, prev))
            start = j
        prev = j
    clusters.append((start, prev))

    claps = []
    for a, b in clusters:
        seg = rms[a : b + 1]
        peak_offset = int(np.argmax(seg))
        peak_idx = a + peak_offset
        claps.append(Clap(float(times[peak_idx]), float(rms[peak_idx])))

    duration_s = float(times[-1]) if len(times) else 0.0
    if duration_s > 0 and len(claps) / duration_s > MAX_CLAPS_PER_SECOND:
        logger.warning(
            "detect_claps: %d events in %.1fs exceeds MAX_CLAPS_PER_SECOND=%.1f, discarding all (threshold=%.1f, median=%.1f)",
            len(claps), duration_s, MAX_CLAPS_PER_SECOND, threshold, median,
        )
        return []

    logger.info(
        "detect_claps: found %d event(s) (threshold=%.1f, median=%.1f): %s",
        len(claps), threshold, median, [f"{t:.3f}s/{r:.0f}" for t, r in claps],
    )
    return claps


def pair_claps(clap_times: list[float]) -> tuple[list[tuple[float, float]], Optional[float]]:
    """Chronological pairing: (times[0], times[1]) is pair 1,
    (times[2], times[3]) is pair 2, etc. A trailing odd-one-out (no
    corresponding hit yet, or ever, in this clip) is returned separately
    - still worth a timestamp label, just no line/speed."""
    pairs = [(clap_times[i], clap_times[i + 1]) for i in range(0, len(clap_times) - 1, 2)]
    trailing = clap_times[-1] if len(clap_times) % 2 == 1 else None
    return pairs, trailing


def puck_speed_kmh(shot_t: float, hit_t: float, distance_m: float = PUCK_TRAVEL_DISTANCE_M) -> Optional[float]:
    dt = hit_t - shot_t
    if dt < MIN_PAIR_INTERVAL_S:
        return None
    return distance_m / dt * 3.6
