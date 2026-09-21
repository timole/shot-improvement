"""Replays a WAV captured by the Android app through the desktop detector
(core/claps.py), so the phone's constants can be re-tuned against real
on-ice recordings and a Kotlin-vs-Python disagreement can be diagnosed.

    python tools/analyze_wav.py shot-20260921120000.wav
    python tools/analyze_wav.py shot-*.wav --distance 22.5

The phone stands at the shooter, so the board impact is heard after the
flight PLUS the sound's return trip: dt = d/v + d/c. The desktop
puck_speed_kmh ignores that term (its mic is at the goal); both numbers
are printed. The desktop detector's thresholds and 0.15 s spectral window
differ slightly from the Kotlin port's, so event times can differ a little
on marginal clips - the sidecar .json next to each WAV has what the app
itself detected.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.claps import detect_claps, pair_claps, puck_speed_kmh  # noqa: E402

SPEED_OF_SOUND_MS = 337.4  # keep in step with Geometry.SPEED_OF_SOUND_MS
MIN_PLAUSIBLE_KMH = 40.0
MAX_PLAUSIBLE_KMH = 170.0
MIN_FLIGHT_S = 0.30


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected 16-bit PCM")
        channels = w.getnchannels()
        rate = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if channels > 1:
        audio = audio.reshape(-1, channels)
    return audio, rate


def shooter_speed_kmh(dt: float, distance_m: float) -> float | None:
    """v = d / (dt - d/c), or None when no plausible flight time remains."""
    flight = dt - distance_m / SPEED_OF_SOUND_MS
    if flight < MIN_FLIGHT_S:
        return None
    return distance_m / flight * 3.6


def hit_window(distance_m: float) -> tuple[float, float]:
    sound = distance_m / SPEED_OF_SOUND_MS
    return (
        distance_m / (MAX_PLAUSIBLE_KMH / 3.6) + sound,
        distance_m / (MIN_PLAUSIBLE_KMH / 3.6) + sound,
    )


def analyze(path: Path, distance_m: float) -> None:
    audio, rate = read_wav(path)
    print(f"{path.name}: {len(audio) / rate:.2f} s @ {rate} Hz")

    claps = detect_claps(audio, rate, distance_m)
    times = [c.time_s for c in claps]
    print("  events:", ", ".join(f"{t:.3f}s" for t in times) or "none")

    # Pair with the shooter-side window (measured dt includes d/c), not the desktop one.
    lo, hi = hit_window(distance_m)
    shots: list[tuple[float, float | None]] = []
    pending: float | None = None
    for t in times:
        if pending is not None and lo <= t - pending <= hi:
            shots.append((pending, t))
            pending = None
            continue
        if pending is not None:
            shots.append((pending, None))
        pending = t
    if pending is not None:
        shots.append((pending, None))

    for shot_t, hit_t in shots:
        if hit_t is None:
            print(f"  shot {shot_t:.3f}s: no hit")
            continue
        dt = hit_t - shot_t
        corrected = shooter_speed_kmh(dt, distance_m)
        naive = puck_speed_kmh(shot_t, hit_t, distance_m)
        corr = f"{corrected:.1f} km/h" if corrected is not None else "n/a"
        print(f"  shot {shot_t:.3f}s -> hit {hit_t:.3f}s  dt={dt:.3f}s  corrected={corr}  uncorrected={naive:.1f} km/h")

    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        app = json.loads(sidecar.read_text(encoding="utf-8"))
        print(f"  app: clapTimes={app.get('clapTimes')} speedKmh={app.get('speedKmh')} "
              f"source={app.get('audioSource')} effects={app.get('effects')}")
    # Also unused desktop pairing, kept for a quick sanity comparison.
    if not times:
        return
    desktop = pair_claps(times, distance_m)
    print(f"  (desktop pairing window would give: {desktop})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", nargs="+", type=Path)
    parser.add_argument("--distance", type=float, default=22.5, help="shot distance in metres (default 22.5)")
    args = parser.parse_args()
    for path in args.wav:
        analyze(path, args.distance)


if __name__ == "__main__":
    main()
