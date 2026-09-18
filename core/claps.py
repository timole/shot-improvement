"""Detects loud percussive audio events ("claps") in a clip - a puck
leaving the stick ("shot") or hitting the boards at the far end of the
rink ("hit") - and turns each shot with a plausible later hit into a
measured puck speed. See specs 098 and 111.

v1 (spec 098) used a simple "cluster by time gap, one peak per
cluster" detector plus naive chronological pairing (clap 0&1 = pair 1,
2&3 = pair 2, ...). Both were found broken against a second real
recording with user-supplied ground truth (specs 111):

- **Clustering** merged genuinely distinct events whenever the audio
  between them never dropped back under the adaptive threshold for a
  full MIN_SEPARATION_S - true on a real clip, where reverb/decay
  between a shot and its own hit (~3s apart) stayed above threshold
  the whole way through, along with a third event. Only the single
  loudest sample of that whole ~4.5s span survived; two real, marked
  events (a shot and its hit) were silently dropped.
- **Chronological pairing** broke two ways on the same real clip: a
  weak unrelated blip 0.7s after a real shot got paired with it
  instead of the real hit 2.8s later, and - once six real shots were
  confirmed (only two with an audible hit) - pairing every two
  consecutive claps started pairing one real shot with the NEXT real
  shot as if it were a hit.

Both are replaced below, validated against two independent real
recordings with real marked ground truth (this session's
shot-improvement-20260918133853.mp4, 6 shots/2 hits at
3.238/6.191/7.365/10.191/11.302/15.429/19.556/23.714, and the original
spec-098 clip).

**Detection** is now non-maximum suppression (NMS) over every above-
threshold RMS sample, not a "one peak per contiguous run" cluster -
this alone recovers events sharing a continuously-elevated span, but
also picks up every noise blip that clears the (amplitude-only)
threshold, e.g. skating/footstep sounds and a stick's own "windup"
tap against the ice just before a real shot. A second gate - the
fraction of that instant's FFT energy in SHOT_BAND_LOW_HZ..
SHOT_BAND_HIGH_HZ - rejects those: measured on the real clip, every
confirmed shot/hit scored >=0.18 in that band, while confirmed non-
shot noise (skating, etc.) topped out at 0.16, and after widening
MIN_SEPARATION_S to 0.5s (to merge a stick-windup tap into its own
shot's peak - a windup and its shot were both loud AND both passed
the spectral gate on their own, only their absolute timing and
relative loudness told them apart), zero false positives and zero
missed real events remained on this clip.

Known limitation, found and accepted (not fixed): the second real
clip's own hit-then-next-shot gap happens to also fall around ~0.43s,
the same neighborhood as clip one's windup-to-shot gap - a purely
local, per-instant feature (timing, amplitude, spectrum) cannot always
tell "one event's windup, followed by its own release" apart from "one
pair's hit, followed by the next pair's shot" when they land at
similar gaps. This module optimizes for the data it was validated
against, not a guarantee for every possible cadence.

**Pairing** is now a greedy state machine over MIN_HIT_DELAY_S..
MAX_HIT_DELAY_S (a physically plausible puck-travel window for this
rink, derived from the two confirmed real hits: 2.826s and 2.953s) -
not "every two consecutive claps": a shot only pairs with a LATER clap
that lands in that window; anything else (too soon, too late, or the
clip just ending) leaves that shot with no hit, wherever it falls in
the clip, not just at the very end.
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
# Spec 111: the minimum gap between two accepted events - non-maximum
# suppression, not "cluster contiguous above-threshold runs" (v1's
# approach, which could merge a real shot and its own real hit into one
# event if the audio between them never dipped under threshold - see
# module docstring). Widened from spec 098's 0.25s to 0.5s specifically
# to also merge a stick's own "windup" tap into its immediately
# following real shot (measured 0.36-0.43s apart, both independently
# loud enough and spectrally shot-like enough to otherwise register as
# two events) - see the module docstring's own "known limitation" for
# the one real case this trades off against.
MIN_SEPARATION_S = 0.5
# Spec 111: a real shot/hit's FFT energy in this band, as a fraction of
# its total energy, cleanly separated confirmed real events (>=0.18)
# from confirmed non-shot noise like skating/footsteps (<=0.16) on a
# real recording - see module docstring for the actual measured values.
SHOT_BAND_LOW_HZ = 2000.0
SHOT_BAND_HIGH_HZ = 8000.0
# Wider than RMS_WINDOW alone - the characteristic mid/high-frequency
# "ring" of a real impact sometimes follows slightly after its own
# loudest (broadband) instant, not exactly at it (measured: a
# confirmed real hit's exact RMS-peak sample scored 0.13 in-band, the
# same instant analyzed over this wider window scored 0.18-0.19).
SHOT_BAND_WINDOW_S = 0.15
SHOT_BAND_FRACTION_MIN = 0.15
# Guards a pathological wall-of-noise clip - if detected events would be
# denser than this, something is wrong with the input, not the clip.
MAX_CLAPS_PER_SECOND = 2.0
# Spec 111: a shot's hit must land in this window to count as a real
# pair - derived from two confirmed real (shot, hit) gaps on this rink,
# 2.826s and 2.953s, with margin on both sides: comfortably wide enough
# to also cover the original spec-098 clip's own two cleanly-plausible
# pairs (3.170s, 3.576s), narrow enough to exclude both a same-shot
# windup-to-release gap (~0.4s) and this clip's own real shot-to-next-
# shot cadence (~4.13-4.17s, so a shot with no real hit never gets
# wrongly paired with the shot that follows it).
MIN_HIT_DELAY_S = 1.5
MAX_HIT_DELAY_S = 4.0
# Guards puck_speed_kmh's division - shouldn't be reachable given
# MIN_HIT_DELAY_S, but cheap to guard explicitly.
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


def _shot_band_fraction(audio: np.ndarray, sample_rate: int, center_t: float) -> float:
    """Fraction of the FFT energy in [SHOT_BAND_LOW_HZ, SHOT_BAND_HIGH_HZ)
    within a SHOT_BAND_WINDOW_S window centered on center_t - see module
    docstring for why this separates a real shot/hit's impact from
    other loud rink noise. 0.0 for a window too short to be meaningful
    (near a clip's very start/end)."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    half = int(SHOT_BAND_WINDOW_S / 2 * sample_rate)
    center_i = int(center_t * sample_rate)
    start = max(0, center_i - half)
    end = min(len(audio), center_i + half)
    chunk = audio[start:end].astype(np.float64)
    if len(chunk) < 8:
        return 0.0
    windowed = chunk * np.hanning(len(chunk))
    spectrum = np.abs(np.fft.rfft(windowed))
    total = spectrum.sum()
    if total <= 0:
        return 0.0
    freqs = np.fft.rfftfreq(len(windowed), 1 / sample_rate)
    band = (freqs >= SHOT_BAND_LOW_HZ) & (freqs < SHOT_BAND_HIGH_HZ)
    return float(spectrum[band].sum() / total)


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

    # Non-maximum suppression across every above-threshold sample, not
    # just within a contiguous run - see MIN_SEPARATION_S's own
    # docstring for why. Each accepted peak also has to clear the
    # spectral gate (SHOT_BAND_FRACTION_MIN) to be counted as a real
    # event, not just loud rink noise.
    candidates = idx.copy()
    candidate_rms = rms[candidates]
    candidate_times = times[candidates]
    claps: list[Clap] = []
    while len(candidates) > 0:
        best = int(np.argmax(candidate_rms))
        peak_t = float(candidate_times[best])
        peak_rms = float(candidate_rms[best])
        if _shot_band_fraction(audio, sample_rate, peak_t) >= SHOT_BAND_FRACTION_MIN:
            claps.append(Clap(peak_t, peak_rms))
        keep = np.abs(candidate_times - peak_t) > MIN_SEPARATION_S
        candidates, candidate_rms, candidate_times = candidates[keep], candidate_rms[keep], candidate_times[keep]
    claps.sort(key=lambda c: c.time_s)

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


def pair_claps(clap_times: list[float]) -> list[tuple[float, Optional[float]]]:
    """Every detected shot's (shot_t, hit_t), in chronological order by
    shot_t - hit_t is None when no later clap lands within
    [MIN_HIT_DELAY_S, MAX_HIT_DELAY_S] of it (too quiet to have an
    audible hit at all, or simply none in this clip).

    A greedy state machine, not spec 098's original "pair every two
    consecutive claps": once a shot is pending, the next clap completes
    it only if the gap is physically plausible for this rink's puck-
    travel time; otherwise the pending shot is left with no hit -
    wherever it falls in the clip, not just at the very end - and that
    next clap starts a fresh pending shot. See module docstring for why
    naive consecutive pairing broke on a real clip."""
    shots: list[tuple[float, Optional[float]]] = []
    pending: Optional[float] = None
    for t in clap_times:
        if pending is not None and MIN_HIT_DELAY_S <= (t - pending) <= MAX_HIT_DELAY_S:
            shots.append((pending, t))
            pending = None
            continue
        if pending is not None:
            shots.append((pending, None))
        pending = t
    if pending is not None:
        shots.append((pending, None))
    return shots


def puck_speed_kmh(shot_t: float, hit_t: float, distance_m: float = PUCK_TRAVEL_DISTANCE_M) -> Optional[float]:
    dt = hit_t - shot_t
    if dt < MIN_PAIR_INTERVAL_S:
        return None
    return distance_m / dt * 3.6
