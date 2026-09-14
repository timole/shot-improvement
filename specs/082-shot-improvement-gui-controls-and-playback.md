# 082 — Shot-improvement GUI controls, native playback, and a real annotation bug fix

**Status:** In progress

## What

Four changes to `experiments/shot-improvement/gui.py`/`core/`:

1. **Bug fix**: annotated recordings were sometimes silently missing
   their palm boxes for most of the clip (see "Why" for how this was
   found and confirmed). `LiveSession` now creates a fresh
   `PoseDetector` right before each recording instead of reusing the
   one long-lived instance shared with the idle preview.
2. **Duration + record split into two controls**: the old single
   "Nauhoita 3 sekuntia" button is now a `ttk.Spinbox` (editable, or
   step with the up/down arrows, default 3, 1-60s) plus a separate
   "Tallenna" button.
3. **Device selection dropdowns**: "Kamera", "Mikrofoni", "Kaiutin"
   (speaker) comboboxes, defaulting to whatever
   `pick_video_device`/`pick_audio_device`/`pick_output_audio_device`
   auto-selected (name match on "logitech"/"jabra", else a fallback),
   editable at any time (camera switch reopens the device live;
   mic/speaker just update which device the next
   recording/playback uses). The camera's real negotiated resolution
   and fps are shown next to the dropdown - cameras are now opened
   requesting `REQUESTED_FPS_CEILING` (60) and the code reads back
   whatever was actually granted, never assuming a number.
4. **Native in-window playback**: "Toista" (or double-click) no
   longer shells out to the OS's default video player - it now plays
   the clip directly in the same preview area used for the live feed,
   decoding video frames with OpenCV (paced to the clip's own fps) and
   audio separately (extracted to a temp WAV via ffmpeg, played with
   `sounddevice.play()` through the selected speaker). "Pysäytä
   toisto" stops it early.

## Why

**The annotation bug**: the user reported live preview showed boxes
correctly but a saved `-annotated.mp4` had none. Confirmed as real
(not user error) by: `md5sum` showing some raw/annotated pairs were
byte-identical (no detections that whole clip) while others weren't;
directly diffing a non-identical pair frame-by-frame found a real box
in frames 0-1 that vanished for the rest of the clip; the exact same
raw frames, re-processed standalone through a *fresh* `PoseDetector`,
detected correctly on every one of them (proving the pixel content
was fine). The one reliable-vs-unreliable split across every test
was: `core.recorder.record_clip()` (CLI) always creates a brand-new
`PoseDetector` per call and never showed this; `LiveSession` (GUI)
reused one detector across the whole app session (idle preview +
every recording) and did. Recreating it per-recording matches the
proven-reliable pattern. The exact MediaPipe-internal mechanism for
*why* a long-lived `VIDEO`-mode detector degrades wasn't pinned down
(switching to stateless `IMAGE` mode was tried and tested *worse*,
more erratic, on the same footage - ruled out) - this is an
evidence-based mitigation, not a root-caused fix.

**Duration/record split, dropdowns, resolution+fps display**: explicit
user request - a fixed 3s was a hello-world simplification, and with
two cameras now genuinely present (a generic "USB Camera" and the
actual Logitech, DirectShow-named "c922 Pro Stream Webcam" - neither
matches "logitech" as a substring) auto-detection alone can't
reliably pick the right one; manual selection needed a real control.

**Native playback**: explicit user request - `os.startfile()` handed
the clip to a separate external application; the ask was to review a
recording without leaving the app.

## Out of scope and known constraints

- The pose-detector-staleness bug's root cause inside MediaPipe was
  not identified - only mitigated. If it recurs even with a fresh
  detector per recording, that would disprove this spec's fix and
  need fresh investigation.
- Playback audio/video sync is approximate (frame display paced by
  wall-clock against the file's own fps; audio started once via
  `sd.play()` and left to run independently) - fine for reviewing a
  few-second personal clip, not frame-accurate.
- Switching the camera or mic/speaker mid-recording is blocked
  (`switch_video_device` raises if `is_recording`); the dropdowns stay
  interactive during playback but changing them has no effect on an
  already-playing clip.
- `REQUESTED_FPS_CEILING` (60) is only ever a request - devices that
  don't support it negotiate down, which is exactly what the
  resolution/fps label next to the camera dropdown is for (showing the
  real, granted value, never the requested one).

## Acceptance criteria

1. A recording made through the GUI has its palm boxes present
   throughout the annotated clip, not just the first frame or two.
   `[T-082-01]`
2. The duration control is a spinbox (typeable, steppable via
   up/down) defaulting to 3, separate from a "Tallenna" button that
   starts a recording of that many seconds. `[T-082-02]`
3. Camera/microphone/speaker dropdowns are populated from the real
   enumerated devices and default to the auto-detected choice;
   changing the camera dropdown live-switches the open device and
   updates the shown resolution/fps to the newly negotiated values.
   `[T-082-03]`
4. "Toista" (or double-clicking a recording) plays it back inside the
   app's own preview area, with audio, without launching any external
   program; "Pysäytä toisto" stops it early and returns to the live
   preview. `[T-082-04]`

## Test plan

- `[T-082-01]` live/manual + code-level: confirmed by direct
  investigation during development (md5sum + frame diffing + a
  from-scratch repro feeding known-good footage through the exact
  pipeline code) - see "Implemented" for exactly what was checked; no
  automated regression test exists for this (it's inherently about
  MediaPipe's live, stateful behavior, not reproducible offline).
- `[T-082-02]` code-reviewed (a `ttk.Spinbox` + separate button is
  standard, low-risk Tkinter wiring); not click-tested live this
  round.
- `[T-082-03]` live/manual, verified via real Win32-click GUI
  automation: opened the camera dropdown, selected the real Logitech
  ("1: c922 Pro Stream Webcam"), confirmed via screenshot the live
  feed changed to that camera's actual view and the resolution/fps
  label updated to "640x480, 60.0 fps".
- `[T-082-04]` the underlying mechanism (ffmpeg audio extraction +
  paced `cv2.VideoCapture` reads + `sounddevice.play()`) was verified
  directly against a real saved recording (correct sample count/
  duration, correct frame count at the file's own fps); the actual
  button/double-click wiring was not confirmed by a live click this
  round (GUI window automation became unreliable - inconsistent DPI
  reporting made two separate script invocations disagree on the
  window's own screen coordinates - see "Implemented").

## Implemented

Shipped in `experiments/shot-improvement/gui.py` (rewritten:
device combos + resolution/fps label, duration spinbox + Tallenna
button, `_tick()`/`_live_tick()`/`_playback_tick()` mode dispatch for
native playback, `stop_playback`), `core/session.py`
(`switch_video_device`, `set_audio_device`, `set_output_device`,
`camera_info`, fresh-`PoseDetector`-per-recording), `core/recorder.py`
(`open_camera` requesting `REQUESTED_FPS_CEILING`,
`pick_output_audio_device`), `core/devices.py`
(`list_output_audio_devices`, `find_output_audio_device_index`).

`[T-082-01]`'s bug was confirmed real through direct investigation on
this laptop (not assumed from the user's report): `md5sum` across all
five then-existing recording pairs showed 3 were byte-identical
raw/annotated (meaning zero detections that whole clip - a red
herring, not this bug) and 2 genuinely differed; frame-by-frame
diffing the non-identical pair found a real green box in frames 0-1
that was absent from frames 2-18 despite the person's hands staying
clearly visible on camera the whole time (confirmed visually); the
exact same raw frames, fed through a *fresh* `PoseDetector` in a
standalone script, detected correctly on nearly every frame. Ruled
out: seek-accuracy artifacts in the verification method itself
(re-checked with accurate, not fast, ffmpeg seeking - same result);
250-idle-blank-frames-before-real-frames as a trigger (reproduced,
detection recovered fine); `IMAGE` running mode as an alternative fix
(tested on the same footage - performed worse, not better).

`[T-082-03]` verified live: launched the GUI, screenshotted to
confirm the three dropdowns populated correctly (mic/speaker showing
"10:"/"12: Kaiun poistava kaiutinpuhelin (Ja..." - the Jabra,
auto-selected once it was connected), opened the camera dropdown,
clicked "1: c922 Pro Stream Webcam" via simulated Win32 mouse events,
and confirmed via a follow-up screenshot both that the live feed
genuinely changed (a different physical camera's view) and the
resolution/fps label updated to "640x480, 60.0 fps".

`[T-082-02]` and the click/double-click wiring for `[T-082-04]` were
not live-click-verified this round; `[T-082-04]`'s underlying
mechanism was verified directly (see "Test plan"). All 14 of this
experiment's own unit tests and 202 of the root repo's `server/tests/`
pass unchanged.
