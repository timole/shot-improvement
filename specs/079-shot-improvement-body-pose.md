# 079 — Shot-improvement body/pose tracking

**Status:** Removed — the whole browser-based approach (this spec and
078) was replaced by a native Python app due to this laptop's limited
memory; see [spec 080](080-shot-improvement-native-python.md). The
palm-center detection approach described here carries over unchanged,
just running natively instead of in a browser tab. Kept here as the
historical record of what was built and why.

## What

The `/shot-improvement` page's sole use case (spec 078's keystroke/
fingertip-tap use case was removed — see its "Implemented" note): an
always-on camera/mic stream, live from page load, and a "Nauhoita 3
sekuntia" button that records a 3-second clip while continuously
marking each visible hand's palm with a green box, every frame — not
a discrete "tap" event, just "where is the hand right now." Detection:
MediaPipe `PoseLandmarker` (BlazePose, the "lite" model variant),
loaded client-side from a CDN. The palm position is estimated per
hand by averaging the wrist landmark with the pinky- and index-finger
knuckle landmarks on the same side (BlazePose gives no palm-center
point directly). Raw and annotated (green-box-burned-in) clips are
both shown after recording; the raw clip is saved into
`experiments/shot-improvement/recordings/` via the same
`POST /api/shot-improvement/save-recording` endpoint spec 078
introduced.

This is the first step toward the actual goal: recognizing a padel
racket's or hockey stick's motion in a short (3-10s) clip. The framing
this spike is meant to tolerate: hockey filmed from the side (at least
the lower hand and the stick's bend visible), padel with the hand
visible. Both are full-body/distance shots, unlike spec 078's
close-up hands-on-keyboard framing.

## Why

Spec 078's `HandLandmarker` is tuned for close-up hands and won't
generalize to a whole-body sports action shot. `PoseLandmarker` was
chosen instead of switching to a different runtime/library because
it's in the same MediaPipe Tasks Vision family (same CDN, same
integration pattern); it's designed for whole-body scenes at a
distance, matching the hockey/padel framing; the "lite" variant is
tuned for real-time inference on modest hardware; and it gives
shoulder/elbow/hip landmarks for free alongside the wrist, likely
useful later when correlating the full swing kinematics with the
stick/racket's own motion, not just the hand.

Tracking just the palm (continuous box, no event detection) is
deliberately the smallest possible step — proving the model choice and
the capture pipeline work end-to-end on real hockey/padel-style
footage before attempting anything about the stick/racket itself.

## Out of scope and known constraints

- Only the palm position (estimated from wrist + pinky-knuckle +
  index-knuckle landmarks, BlazePose indices 15/17/19 and 16/18/20) is
  used for now — not the full 33-point body topology, and not the
  racket/stick itself (there is no racket/stick detection at all yet).
- No smoothing/interpolation — the box can jitter or disappear frame-
  to-frame exactly like the underlying model's per-frame confidence
  does; a hand is skipped that frame if any of its three landmarks is
  below `VISIBILITY_THRESHOLD` (0.5).
- Same capture/recording infrastructure and constraints as spec 078
  (always-on stream from page load, camera/mic device fallback and
  caching, save-to-repo with a browser-download fallback, on-device-
  only inference) — see that spec for the shared details.

## Acceptance criteria

1. `GET /shot-improvement/` returns a page with a "Nauhoita 3
   sekuntia" button, disabled until the camera/mic stream is ready.
   `[T-079-01]`
2. Clicking it records 3 seconds, drawing a green box over the
   estimated palm center of each visible hand every frame throughout.
   `[T-079-02]`
3. After recording, both a raw and an annotated (green-box-burned-in)
   `<video controls>` are shown. `[T-079-03]`
4. The raw clip is saved to
   `experiments/shot-improvement/recordings/shot-improvement-<YYYYMMDDHHmmss>.webm`
   via `POST /api/shot-improvement/save-recording`. `[T-079-04]`

## Test plan

- `[T-079-01]`, `[T-079-04]` offline: `TestClient(app).get("/shot-improvement/")`
  asserts "PoseLandmarker" appears in the body
  (`server/tests/test_main.py::test_shot_improvement_mount`);
  `POST /api/shot-improvement/save-recording` with a
  `shot-improvement-<14 digits>.webm` filename returns 200 and writes
  the file (`test_shot_improvement_save_recording`).
- `[T-079-02]`, `[T-079-03]` live/manual: open
  `https://ai.timolehtonen.tech/shot-improvement/` (or localhost),
  film a hand moving (ideally an actual padel/hockey framing - side
  view for hockey, hand visible for padel), click record, verify green
  boxes track the visible hand(s)' palms through the clip in both the
  live view and the saved annotated clip.

## Implemented

Shipped in `experiments/shot-improvement/web/index.html`
(`startDetectionLoop` using `PALM_LANDMARKS`, `PoseLandmarker` loader)
and `server/main.py` (`_SHOT_IMPROVEMENT_FILENAME_RE`). Originally
built alongside spec 078's keystroke use case (sharing the page,
distinguished by a `-pose-` filename infix); after that use case was
removed, this became the page's only content and the shared
infrastructure (button, save pipeline, filename pattern) was
simplified back down to one flow. Initial wrist-only marking was
refined to a palm-center estimate (wrist + pinky-knuckle +
index-knuckle average) per explicit request. `[T-079-01]` and
`[T-079-04]` verified via `server/tests/test_main.py`. `[T-079-02]`,
`[T-079-03]` need a live manual check with real padel/hockey-style
footage — not yet run.
