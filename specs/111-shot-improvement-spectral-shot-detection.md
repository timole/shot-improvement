# 111 — Shot-improvement: spectral shot detection, physically-windowed pairing

## What

`core/claps.py`'s detection and pairing are both rewritten:

- **Detection**: non-maximum suppression (NMS) over every above-
  threshold RMS sample (not spec 098's "cluster a contiguous
  above-threshold run, take its single loudest sample"), gated by a
  new spectral check - a real event's FFT energy fraction in
  2000-8000Hz must be at least 0.15 to count, which rejects loud
  non-shot noise (skating, footsteps) that clears the amplitude
  threshold but doesn't have a shot/hit's spectral shape.
- **Pairing**: a greedy state machine matching each shot to the next
  clap only if it lands within a physically plausible puck-travel
  window (1.5-4.0s), replacing spec 098's "pair every two consecutive
  claps" - a shot can now correctly end up with no hit anywhere in the
  clip, not just as the very last one.

## Why

Investigated at the user's request: a real recording
(shot-improvement-20260918133853.mp4) with real Audacity-marked ground
truth (6 shots: 3.238/7.365/11.302/15.429/19.556/23.714s, only 2 with
an audible hit: 6.191s and 10.191s) exposed two real bugs in spec 098's
original algorithm:

1. Contiguous-run clustering merged a real shot, its own real hit, and
   the NEXT shot into one giant span (the audio between them never
   dropped back under the adaptive threshold - ambient rink
   reverb/decay) - only the single loudest sample of that ~4.5s span
   survived, silently dropping two real, marked events.
2. Naive "pair every two consecutive claps" broke two ways: a weak
   unrelated blip 0.7s after a real shot got treated as its hit
   instead of the real one 2.8s later, and - once all 6 real shots
   were confirmed, only 2 with hits - it started pairing one real shot
   with the NEXT real shot as if it were a hit.

## Design

See `core/claps.py`'s own module docstring for the full validated
numbers (measured against two independent real recordings) and the
one known, accepted limitation: a purely local, per-instant audio
feature can't always distinguish "one event's own windup, followed by
its own release" from "one pair's hit, followed by the next pair's
shot" when both happen to land around the same ~0.4s gap - this
algorithm is tuned against the data it was validated on, not
guaranteed for every possible shot cadence.

`core.compose.pair_claps()`'s return type changed from
`(pairs, trailing_unpaired: Optional[float])` to a single
`list[tuple[float, Optional[float]]]` - every shot in chronological
order, `hit_t` is `None` when unpaired. Both call sites
(`compose_annotated_frames`, `save_shot_images`) updated; no other
files called `pair_claps` directly.

## Files

- `core/claps.py` - rewritten `detect_claps()`/`pair_claps()`, new
  `_shot_band_fraction()`, new constants (`SHOT_BAND_LOW_HZ`,
  `SHOT_BAND_HIGH_HZ`, `SHOT_BAND_WINDOW_S`, `SHOT_BAND_FRACTION_MIN`,
  `MIN_HIT_DELAY_S`, `MAX_HIT_DELAY_S`), `MIN_SEPARATION_S` widened
  0.25s -> 0.5s.
- `core/compose.py` - both `pair_claps()` call sites updated for the
  new return type.
- `tests/test_claps.py` - updated for the new API and tuning; added a
  second real-clip regression test pinning this session's own ground
  truth (2 pairs, 4 unpaired shots, correct speeds).

## Test plan

- `venv\Scripts\pytest tests\` - 112 passed.
- Directly validated `detect_claps()` against both real recordings'
  actual audio (not just the pinned event-list tests, which feed a
  fixed list straight into `pair_claps` and don't exercise detection
  at all): on shot-improvement-20260918133853.mp4 it reproduces all 6
  real shots + 2 real hits exactly, zero false positives or misses. On
  the original spec-098 clip it finds 8 of the 10 previously-known
  events - missing 10.037s and 25.275s, both losses to the accepted
  0.435s-gap tradeoff documented above (each sits within
  MIN_SEPARATION_S of a much louder real neighbor - 10.472s and
  26.720s - and gets suppressed by it). That clip's own video isn't
  part of today's reprocessing and wasn't regenerated; the pinned
  `test_real_clip_event_list_pairs_to_plausible_speeds` test still
  passes because it exercises `pair_claps` directly against the
  already-known event list, not `detect_claps` against the audio.
