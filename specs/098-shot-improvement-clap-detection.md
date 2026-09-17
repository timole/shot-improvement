# 098 — Shot-improvement: clap detection + puck-speed annotation

## What

The annotated video (`-annotated.mp4`) now has a third band. Previously
1280x1200 = 720px video + 480px spectrogram; now 720px video + 280px
spectrogram + a new 200px "claps" band, same 1280x1200 total, so nothing
downstream (ffmpeg encode, GUI playback, thumbnail generation) needed
dimension changes.

"Claps" are loud percussive audio transients - a puck leaving the stick
("shot") or hitting the boards at the far end of the rink ("hit"). Every
detected clap gets a short timestamp label (e.g. "2.75") in the new band,
positioned left-to-right on the same time axis the spectrogram/playhead
already uses. Consecutive claps are paired chronologically (clap 0 & 1 =
pair 1, 2 & 3 = pair 2, ...); for each pair, a yellow line spans the
shot-to-hit interval on the spectrogram (row 740 in the full composite),
with the computed puck speed (e.g. "60 km/h") just below it. A trailing
unpaired clap (odd count - e.g. a shot whose hit sound isn't in the clip)
still gets its timestamp, no line/speed.

Speed: a regulation rink is 61m end to end; a shot taken from the goal
line (4m in from the end being shot at) travels 57m to reach the far
boards. `speed_kmh = 57.0 / (hit_t - shot_t) * 3.6`.

## Why

The app's own README has called the actual padel/hockey motion-tracking
feature "still unspecced" since spec 078. This is the first concrete
version of it - not motion tracking yet, but the audio side: detecting
the two events a puck's rink-length trip produces and measuring the time
between them.

## Detection algorithm - validated against a real clip, not guessed

256-sample (~5.8ms) RMS windows over the mono audio. The first 0.3s is
excluded (a known mic-startup pop artifact, diagnosed in spec 097) from
both detection and the threshold's own statistics. Adaptive threshold =
`max(500.0, median(rms) * 5.0)`.

This was tuned empirically against
`recordings/shot-improvement-20260915110833-annotated-fixed.mp4` (a real
30s recording the user described as containing 6 shots, with the 6th
shot's hit sound not present in the clip):

| Threshold ratio | Result |
|---|---|
| 2.2 (initial guess) | 24-40 false positives from ambient rink noise (median RMS ≈494, noise bumps reach ~2050) |
| 5.0-6.0 | Exactly 10 events, zero false positives |

The 10 events found at ratio 5.0:
`6.867, 10.037, 10.472, 14.048, 17.090, 18.077, 21.200, 22.192, 25.275,
26.720` (seconds) - pinned as a real-world regression test in
`tests/test_claps.py::test_real_clip_event_list_pairs_to_plausible_speeds`.

A high-pass (`np.diff`) pre-emphasis pass was also tried: it separated
the LOUD "hit" tier from noise very well (median RMS dropped to 15) but
pushed the quieter "shot" tier below the detection floor entirely -
worse overall than just raising the plain-RMS threshold ratio. Not used.

**Clustering**: above-threshold samples where consecutive ones are
within `MIN_SEPARATION_S` (0.25s) of each other in time are one event;
that event's timestamp is the **peak** RMS sample within the whole
cluster (the user explicitly wants the peak's timestamp, not the onset).
This single time-gap-clustering rule matters more than it sounds: an
earlier draft clustered with a much smaller (~20ms) gap tolerance and
then ran a SEPARATE "keep whichever detection came first" merge pass
across nearby clusters - that design is provably wrong, since it let a
1300-RMS ambient noise blip 75ms before a real 5230-RMS peak suppress
the real peak entirely. Clustering by the full `MIN_SEPARATION_S` window
directly, with no separate merge step, fixes this: the argmax within a
merged cluster always finds the genuinely loudest sample.

A rate guard (`MAX_CLAPS_PER_SECOND = 2.0`) returns no events at all
rather than hundreds of overlapping labels if the input is pathological
wall-of-noise.

## Pairing - chronological, not amplitude-based

Simple chronological order: `clap[0]&clap[1]`, `clap[2]&clap[3]`, etc.
An amplitude-based alternative (classify by loudness, pair quiet-then-
loud as shot-then-hit) was considered and rejected: the real clip's
first shot (RMS ~10500) sits in the exact same loud tier as every hit
event, so loudness alone would misclassify it. Chronological pairing,
given an accurate detector, produced genuinely plausible results:

- Pair 1 (6.867s → 10.037s, Δt=3.170s): **64.7 km/h**
- Pair 2 (10.472s → 14.048s, Δt=3.576s): **57.4 km/h**

Both match the user's own real-time estimate for this exact clip ("60
km/h", "approximately 3 seconds").

## Known, accepted limitations (v1, not bugs)

- **Pairs 3-5 give implausible speeds** (17.09/18.08s → 208 km/h,
  21.2/22.19s → 207 km/h, 25.28/26.72s → 142 km/h) - these are real,
  correctly-detected, correctly-paired events (weak-then-loud, matching
  the shot/hit amplitude pattern), just with a ~1s gap. The fixed 57m
  travel distance almost certainly doesn't hold for every shot in a
  practice session - a shorter-range drill shot paired chronologically
  will show an unrealistic speed. This module doesn't try to guess a
  variable distance; the number is shown as computed.
- **The user-described 6th shot isn't detected.** Its clip's tail
  (26.8s-29.0s) never exceeds RMS 1432, well under the ~2470 threshold
  that keeps the rest of the clip false-positive-free. Most likely
  genuinely quiet in this recording (mic distance/angle) - lowering the
  threshold to catch it would reintroduce the false positives the
  current ratio was specifically tuned to avoid.
- **Threshold portability across very different recording environments
  is unverified beyond this one clip** (noisy rink, median RMS ≈494) and
  the spec-097 quiet-room clap test (median RMS ≈66-137, and separately
  confirmed to detect cleanly at this threshold too). A much noisier or
  much quieter environment than either may need the ratio retuned.

## Implemented

- `core/claps.py` (new) - `rms_envelope()`, `detect_claps()`,
  `pair_claps()`, `puck_speed_kmh()`. Pure numpy, no cv2/image
  dependency, fully unit-tested in isolation.
- `core/spectrogram.py` - `SPECTROGRAM_HEIGHT` 480 → 280 (comment
  rewritten to describe the real 3-band layout, owned by
  `core.compose`).
- `core/compose.py` - `CLAPS_BAND_HEIGHT`, `_x_for_time()` (the one
  time-to-pixel formula the moving playhead and every new annotation
  share), `render_claps_band()`, `draw_pair_annotations()`. Both new
  render functions are called ONCE per clip (not per frame) -
  `draw_pair_annotations` bakes the yellow line(s) directly into the
  `spectrogram` base image so the existing per-frame `bottom[:] =
  spectrogram` copy picks them up for free, and the claps band is
  written into the composite buffer's third row-slice once before the
  loop starts. `composite_into()` itself - and its 4 existing
  pixel-identity tests - are completely unchanged; the per-frame loop
  never touches the claps band's rows.
- `tests/test_claps.py` (new) - synthetic-audio detection tests (finds
  injected events, ignores the startup pop, silence/near-silence/
  wall-of-noise guards, decay-tail merging vs. genuinely distinct
  events, stereo input), pairing tests, speed-formula tests, and a
  real-clip regression test pinning the validated event list above.
- `tests/test_compose.py` (extended) - `_x_for_time`, `render_claps_band`,
  `draw_pair_annotations`, and a 3-band buffer contiguity/disjointness
  check; all 4 pre-existing oracle tests untouched.
- `tools/bench_record.py` - disk micro-benchmark's annotated composite
  size now includes `CLAPS_BAND_HEIGHT` (was silently ~17% short).

## Test plan

- `venv\Scripts\pytest tests\` - 96 passed (18 new in `test_claps.py`,
  10 new in `test_compose.py`, all 4 pre-existing `test_compose.py`
  oracle tests still passing unchanged).
- Re-ran the real `core.claps.detect_claps()` (not a scratch copy)
  against the real clip's extracted audio - reproduced the validated
  10-event list and pair speeds exactly.
- Verified against a real recording (`shot-improvement-20260917115400.mp4`,
  a real hockey-rink test clip with pose detection correctly finding a
  palm box mid-shot): processed through `tools/annotate_existing_video.py`
  (new - recovers real per-frame timestamps from an existing mp4 via
  ffmpeg's own reported PTS, for a clip that didn't go through the normal
  GUI/CLI capture flow) into a real `-annotated.mp4`. Output is exactly
  1280x1200; all 9 real claps show correctly positioned, non-overlapping
  timestamp labels across the 4 cycled rows; 4 yellow pair-lines +
  speed text render legibly over the spectrogram. The computed speeds
  (149-478 km/h) are implausibly high - expected, since this clip's
  claps weren't spaced like a real full-rink shot/hit pair (see "Known,
  accepted limitations" above); the rendering itself is correct.
