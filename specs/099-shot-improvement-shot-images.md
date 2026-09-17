# 099 — Shot-improvement: per-shot snapshot JPEGs

## What

Every recording that gets an annotated clip now also gets one plain
JPEG per detected shot: `shot-improvement-<timestamp>-shot-01.jpg`,
`-shot-02.jpg`, etc., in chronological order. Unlike the annotated
mp4 (which grows a spectrogram/claps band and pose boxes), a shot
image is the plain camera frame at native 1280x720 - no pose boxes,
no spectrogram - showing exactly the video frame closest to that
shot's real peak timestamp (i.e. the moment the stick hits the puck,
per spec 098's clap detection), with the computed puck speed burned
in as white, black-outlined text at the bottom (50px glyph height). A
trailing unpaired shot (no corresponding hit yet, so no computable
speed - see spec 098) still gets an image, just with no speed text.

## Why

A direct follow-up to spec 098: once a shot's exact timestamp and
speed are known, the most useful artifact for actually reviewing a
practice session is a quick snapshot per shot, not just an annotation
baked into a 10+ second video.

## Design

`core.compose.save_shot_images()` (new): takes the same raw frame
directory + per-frame timestamps + audio that `compose_annotated_
frames()` already uses, re-runs `core.claps.detect_claps()`/
`pair_claps()` on the same audio (cheap - ~12ms per spec 098's
profiling, not worth threading the already-computed result through
every caller), and for each shot (every pair's first element, plus a
trailing unpaired shot if present):

1. Finds the RAW frame whose own real capture timestamp
   (`frame_times`) is closest to the shot's peak timestamp
   (`np.argmin(np.abs(frame_times - shot_t))`) - not an assumed index,
   consistent with spec 097's "frames aren't evenly spaced" finding.
2. Reads that frame directly from the raw frames directory - genuinely
   unannotated, since `composite_into()` only ever mutates an
   in-memory copy for the annotated composite, never the raw file on
   disk.
3. Burns in `"NN km/h"` (white, black-outlined, target 50px glyph
   height computed via one `cv2.getTextSize` reference measurement,
   since cv2 only exposes an abstract font "scale") at the bottom,
   horizontally centered - skipped for the trailing unpaired shot,
   which has no computable speed.
4. Saves as a JPEG (quality 92) at
   `<out_dir>/<filename_stem>-shot-NN.jpg` (2-digit, zero-padded).

Wired into all three places a recording finishes
(`core.recorder.record_clip`, `core.session.LiveSession._finish_
immediate_recording`, `core.pending.process_pending_recording`) -
right after each one's existing `compose_annotated_frames()` call,
while the raw frames are still on disk.

## New tool: `tools/annotate_existing_video.py`

Also gained this feature (spec 098's tool, extended) - useful for a
clip that didn't go through the normal capture flow (e.g. supplied
directly rather than recorded via the GUI/CLI, which was this spec's
own first real test case).

## Test plan

- `venv\Scripts\pytest tests\` - 100 passed (4 new in
  `test_compose.py`: one image per paired shot, correct nearest-frame
  selection by real time, trailing unpaired shot gets an image with no
  speed text, no claps -> no images).
- Processed a real clip (`shot-improvement-20260917120000.mp4`,
  supplied directly rather than recorded through the GUI) via
  `tools/annotate_existing_video.py` - produced its `-annotated.mp4`
  and one shot JPEG per detected shot pair, visually confirmed.
