# 104 — Shot-improvement: variable-frame-rate fix, low-memory `--shots-only` mode

## What

- Fixed a real bug in `tools/annotate_existing_video.py`'s frame
  extraction: a variable-frame-rate source now extracts exactly one
  file per decoded frame (`-fps_mode passthrough`), matching the
  `frame_times` list 1:1. Previously, ffmpeg's default frame-rate
  conversion duplicated frames to match the container's nominal rate,
  silently desyncing the two and making `save_shot_images()` refuse
  every shot ("frame_times/frame_paths mismatch").
- New `--shots-only` flag on the same tool: skips
  `compose_annotated_frames()`'s full per-frame pose-detection pass
  and the final `-annotated.mp4` encode, producing just the per-shot
  JPEGs. Much lighter on memory - `save_shot_images()` only runs pose
  detection on the handful of selected shot frames, not the whole
  clip.

## Why

Testing the pipeline against two real phone videos (a garage and a
backyard hockey shooting setup, both portrait phone screen
recordings, HEVC, variable frame rate) surfaced both issues directly:

- The VFR mismatch: one clip's `showinfo` logged 316 real decoded
  frames, but the image2 muxer wrote 566 files (duplicating frames to
  match the container's 60fps nominal rate against a 33.5fps average
  decode rate) - exactly the ~1.79x ratio between those two numbers.
  Every previous clip processed this session was genuine constant
  frame rate (this app's own webcam capture, or an already-CFR
  export), where duplication never kicks in - this is the first VFR
  source encountered.
- The memory issue: this laptop has ~4GB RAM total (a known
  constraint - see `core.recorder`'s module docstring). The full
  `compose_annotated_frames()` pass got killed for low memory twice in
  a row on one of the two real test clips (~300 frames, a live
  `PoseDetector` running for the whole pass) even with the VFR fix
  applied. `--shots-only` completed both clips without issue
  immediately after.

## Test plan

- `venv\Scripts\pytest tests\` - 100 passed, no test changes needed
  (this is a tool-level fix/feature, not `core/`).
- Frame-count fix verified directly: extracting the same VFR clip
  with vs. without `-fps_mode passthrough` gave 566 vs. 316 files;
  with the fix, files == `showinfo`'s pts_time count exactly (316).
- Both real uploaded videos processed successfully via
  `--shots-only`, producing real, visually-confirmed shot images
  (correct hand-box placement, correct orientation, speed labels) -
  `recordings/screen-recording-20260917171759-shot-01.jpg` through
  `-04.jpg`, and `recordings/screen-recording-20260917172221-shot-01.jpg`
  through `-03.jpg`. Computed speeds (202-384 km/h) are implausibly
  high, as expected for a short-range garage/backyard setup, not a
  full 61m rink (see spec 098's documented distance-assumption
  limitation).
