# 112 — Shot-improvement: manual focus, and a found 30fps ceiling

## What

`core.recorder.open_camera()` now also forces manual focus
(`CAP_PROP_AUTOFOCUS=0`, `CAP_PROP_FOCUS=0` - read back from this
camera's own auto-focus after it settled on a normal indoor scene, not
guessed), alongside spec 109's manual exposure.

## Why

Spec 109's exposure fix alone wasn't enough - a real recording still
came out at 29.7fps. The user asked to fix every remaining auto-
adjustment ("no automatic zoom or lighting settings") and to verify
with real 10s test captures rather than more theorizing.

## What was found (the honest result)

Autofocus is now fixed too, and it's a real, validated improvement
worth keeping - but it did **not** unlock 60fps. With both exposure
and focus manual, real capture throughput lands at a clean, exact
**30.0fps**, confirmed:

- Independent of resolution: 640x360 measured the same exact 30.0fps
  as 1280x720 (ruling out an MJPG-encoder-bandwidth explanation - a
  much smaller frame at the same rate would need far less throughput
  if that were the bottleneck).
- Independent of every other `cv2.CAP_DSHOW` property tried during
  this investigation.
- `cv2.CAP_MSMF` (the newer Windows Media Foundation capture backend)
  was tried once as an alternative to DirectShow - it hung the whole
  process outright on `cap.isOpened()`/first `cap.read()` and had to
  be killed. Not a safe avenue to pursue further without real risk of
  needing another physical USB reset.

This looks like a genuine ceiling of this camera's MJPG mode over
DirectShow on this system - `cap.get(CAP_PROP_FPS)` reporting 60 after
`cap.set(CAP_PROP_FPS, 60)` appears to just echo the request, not
confirm a real negotiated rate; the only trustworthy signal is
measured real throughput, which was consistently, exactly 30fps across
every configuration tried.

**Also found and worth flagging**: this session's own repeated rapid
camera open/close cycling (while probing different property
combinations) pushed the physical device into a genuinely stuck state
- every single `cap.read()` call returning after exactly 1.000s,
regardless of settings, recovered only after a real gap in activity
(a 45s software-level wait was not enough on its own; the issue
cleared before the next attempt, so a physical USB replug may also
have been involved - not confirmed which). If real captured fps ever
looks unexpectedly terrible (not just "capped at 30" but far below
that), a stuck device state - not a code regression - is worth
checking first: try again after leaving the camera alone for a while,
or a physical USB reconnect.

## Files

- `core/recorder.py` - `MANUAL_FOCUS_MODE`/`FIXED_FOCUS` constants,
  applied in `open_camera()` alongside the existing exposure fix; the
  "Opened camera" log line now also reports the real negotiated focus.

## Test plan

- `venv\Scripts\pytest tests\` - 112 passed (camera opening isn't
  exercised without real hardware).
- Multiple real recordings via `record.py` (the CLI path, not the
  GUI - faster to iterate without needing a human to click record)
  confirmed focus is applied (`exposure=-7.0, focus=0.0` in the log)
  and throughput is a consistent ~30fps, not worse than before.
