# 109 — Shot-improvement: force a fixed, short camera exposure

## What

`core.recorder.open_camera()` now forces manual exposure with a short,
fixed value (`CAP_PROP_AUTO_EXPOSURE=0.25` / DirectShow's "manual" mode,
`CAP_PROP_EXPOSURE=-7`, ~1/128s) instead of leaving the C922 on
auto-exposure.

## Why

Investigated a real recording (`shot-improvement-20260918133853.mp4`)
that came out at 10.0 fps instead of the camera's negotiated 60fps.
`ffprobe`/OpenCV confirmed the mp4 itself was encoded correctly - the
capture loop genuinely only delivered ~303 frames over 30.28 real
seconds (`actual_fps = frame_count / elapsed_s`, computed directly in
`core.session._start_finish_recording`).

Ruled out disk I/O (a real BMP write on this disk benchmarked at ~13ms,
fine for 60fps) and the already-fixed FOURCC/bandwidth issue from specs
086/088 (fourcc was correctly negotiated as MJPG at open time). The
smoking gun, from the log across the whole session: throughput dropped
from ~32fps to ~10-14fps between two recordings **in the same
continuous camera session, with no reopen in between**, and stayed low
for every recording afterward. That rules out a one-time negotiation
quirk (which only runs once, at `open_camera()`) and points at the
camera's own auto-exposure lengthening its per-frame exposure time
under the room's ambient light - a well-known UVC webcam behavior that
mechanically caps the deliverable frame rate regardless of the
negotiated MJPG/60fps capability.

The user's explicit call: frame rate matters more than image quality
for this app (measuring fast motion), so force a short exposure and
accept a darker/possibly-blurrier picture.

## Design

Two new constants in `core/recorder.py`, next to `REQUESTED_FOURCC`:
`MANUAL_EXPOSURE_MODE_DSHOW = 0.25` (DirectShow's own manual-exposure
convention - 0.25/0.75, not V4L2's 1/3) and `FIXED_EXPOSURE_DSHOW = -7`
(DirectShow's log2-scale exposure - `2**-7`s ≈ 7.8ms, comfortably under
one 60fps frame's ~16.7ms budget). Applied in `open_camera()` after
FOURCC, same "set after the capture mode settles" reasoning already
documented there for FOURCC. The real negotiated exposure is now read
back and logged alongside width/height/fps/fourcc, so a future "why is
fps low" investigation doesn't need to re-derive this from scratch.

Not yet empirically re-measured against a real low-light recording
(this machine's camera availability was intermittent when this was
written - see the next recording's `actual_fps` in the log to confirm
it holds near 60fps now). `FIXED_EXPOSURE_DSHOW` is camera/driver-
specific; revisit if a real recording still falls short.

## Test plan

- `venv\Scripts\pytest tests\` - 105 passed (no behavior this suite
  covers changed - camera opening isn't exercised without real
  hardware).
- Record a clip through the GUI and check
  `logs/shot-improvement.log`'s "Opened camera" line for the real
  negotiated exposure, then the recording's own `actual_fps` - should
  be close to 60 regardless of room lighting.
