# 102 — Shot-improvement: drop stick/blade, yellow-only hands, annotate shot images

## What

- **Stick/blade detection and annotation removed entirely** - `core/
  stick.py` and its tests deleted, `core.pose`'s now-unused
  `raw_palm_positions()`/`PoseDetector.detect_with_raw_hands()`
  (added in spec 101 solely to anchor the stick between two hands)
  removed along with it. `tools/annotate_image.py` now only draws
  hand boxes.
- **Hand boxes are yellow now** (`core.pose.BOX_COLOR_BGR`, was
  green).
- **The per-shot snapshot JPEGs (spec 099,
  `core.compose.save_shot_images()`) now get hand-box annotations
  too** - previously a plain camera frame with only the speed label
  burned in; now also shows the yellow hand boxes for that exact
  frame, same as the annotated clip's own boxes.

## Why

Direct user feedback after seeing spec 101's stick/blade result -
remove it, keep only hand detection, and extend hand annotation to
the shot images that were previously left unannotated.

## Shot images: a second PoseDetector, not a threaded-through one

`save_shot_images()` runs well after `compose_annotated_frames()` has
already run pose detection over every frame AND closed its own
`PoseDetector` - by the time `save_shot_images()` is called, there's
no live detector left to reuse, and changing `compose_annotated_
frames()`'s per-frame loop to hand back every frame's boxes (most of
which aren't shot frames at all) purely to serve this second, much
smaller job isn't worth the coupling. `save_shot_images()` instead
opens its own `PoseDetector`, but only runs it over the handful of
selected shot frames (typically well under 10 per clip) - the model-
load cost (~400ms, per spec 092's profiling) is paid once per
recording, not once per frame, and stays cheap relative to the rest
of the pipeline. Guarded to skip creating a detector at all when no
shots were detected.

## Test plan

- `venv\Scripts\pytest tests\` - 100 passed (test_stick.py's 7 tests
  removed along with the module; test_pose.py/test_compose.py
  unaffected by the color change, since neither hardcodes the BGR
  value). The existing `save_shot_images` tests in `test_compose.py`
  use synthetic flat-color frames with no real person in them, so
  pose detection finds nothing there and draws nothing - they weren't
  extended to verify real hand-box drawing in isolation (would need a
  real photo or a detector-injection seam, neither of which exists
  elsewhere in this module's own test style either -
  `compose_annotated_frames` itself isn't unit-tested against a real
  detector, only via `composite_into`'s pure pixel-comparison tests).
  Real hand-box drawing is confirmed instead via the real photos/clip
  below.
- `tools/annotate_image.py` re-run against `recordings/sample-image.jpg`
  and `recordings/sample-image-02.jpg` - yellow hand boxes only, no
  stick/blade annotation.
- `tools/annotate_existing_video.py` re-run against
  `recordings/shot-improvement-20260917120000.mp4` - the regenerated
  `-shot-NN.jpg` files now show yellow hand boxes alongside the speed
  label, visually confirmed.
