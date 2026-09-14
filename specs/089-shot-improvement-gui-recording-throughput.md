# 089 — Shot-improvement: GUI recording throughput + no console windows

## What

Spec 088 fixed real fps for the *headless* `record_clip()` path (CLI,
and the direct `record_clip()` call spec 088 tested with) to ~58fps at
1280x720, but the GUI's actual recording path
(`LiveSession.start_recording()`/`read_frame()`, driven by
`gui.py`'s `_tick()`) was never separately measured and turned out to
still be capped - the first real GUI recording after spec 088 came out
at 16.58fps (`shot-improvement-20260914113807.mp4`), confirmed by the
user checking the file's own properties, not just this app's own
self-reported number. Two GUI-specific costs, on top of spec 088's
camera-level fix:

1. `_live_tick()` called `_show_frame()` (BGR->RGB convert, PIL resize,
   `PhotoImage` rebuild, Tkinter label update) on every frame **even
   while recording**, despite there being nothing to usefully show -
   `LiveSession.read_frame()` already returns the undecorated raw frame
   during recording specifically so the live preview can stay smooth,
   but nothing was skipping the display work itself.
2. `_tick()` always rescheduled itself via `root.after(PREVIEW_POLL_MS,
   ...)` (a flat 10ms gap), which exists to pace the *idle* preview's
   redraws but was also throttling the *recording* loop the exact same
   way - adding ~10ms of dead time on top of each frame's real capture
   cost, well before any camera/pixel-format limit was reached.

Fixing only #1 got real fps to ~44.8fps (measured via a direct
`LiveSession` script mirroring `_live_tick()`, sleeping 10ms between
reads the same way `after(10, ...)` does); fixing #2 too (reschedule
with `root.after_idle(...)` while recording, `after(PREVIEW_POLL_MS,
...)` otherwise) got it to ~56.6fps - matching spec 088's own headless
number.

Separately: ffmpeg's console briefly flashed a window during encoding.
`subprocess.run([ffmpeg_exe, ...])` had no `creationflags`, and a
console-subsystem child process gets its own window by default even
when the parent (`pythonw.exe`) is windowless. Fixed in both call
sites (`core/recorder.py::encode_frames_with_audio`,
`core/playback.py::extract_audio`) with
`creationflags=subprocess.CREATE_NO_WINDOW`.

## Why

The user's own OBS baseline (1280x720@60fps, same camera/machine) is
the standard this app is being held to, and spec 088 alone didn't
reach it in the one place that actually matters - a real recording
made by clicking "Tallenna" in the running app, not a headless script.
The console-window flashes were reported alongside the fps issue in
the same message.

## Out of scope and known constraints

- `root.after_idle()` still goes through Tkinter's own event loop, not
  a truly bare `while` loop - the measured ~56.6fps (vs. the headless
  path's ~58fps) is presumably that small remaining per-tick Tkinter
  dispatch overhead, judged close enough to "lähes 60 fps" (the user's
  own bar) not to chase further.
- No change to idle (non-recording) preview pacing -
  `PREVIEW_POLL_MS=10` still applies there, unaffected by this spec.
- Not verified whether other console-subsystem subprocesses could
  exist in the future (e.g. a new ffmpeg call site) - each call site
  needs its own `creationflags=subprocess.CREATE_NO_WINDOW`, there's no
  single global switch for this in `subprocess`.

## Acceptance criteria

1. A recording made through the actual GUI (`LiveSession.start_
   recording` + repeated `read_frame()`, not the headless `record_
   clip()` CLI path) reaches real fps close to 60 at 1280x720, not the
   ~16.6fps seen right after spec 088. `[T-089-01]`
2. No console window appears while ffmpeg runs (encoding or audio
   extraction) under `pythonw.exe`. `[T-089-02]`

## Test plan

- `[T-089-01]` live: a script creating a real `LiveSession`, calling
  `start_recording()`, then looping `read_frame()` with no sleep
  (matching the fixed `after_idle` cadence) until `is_busy` clears.
  Measured `actual_fps=56.6` (283 frames / 5.0s); the encoded file's
  own ffmpeg-reported stream info confirmed independently: `1280x720,
  ... 56.60 fps ... Duration: 00:00:05.00`.
  An intermediate version of this same script, sleeping 10ms per
  iteration (matching the *old* `after(10, ...)` cadence with only fix
  #1 applied) measured `actual_fps=44.8` - confirming fix #2's
  contribution separately from fix #1's.
- `[T-089-02]` code: both `subprocess.run` call sites now pass
  `creationflags=subprocess.CREATE_NO_WINDOW`; not independently
  re-verified visually this session (the underlying cause -
  console-subsystem child process, windowless parent - is well-known
  Windows `subprocess` behavior, not specific to this app).
- `pytest`: full suite (35 tests) passes unchanged.

## Implemented

- `gui.py`:
  - `_live_tick()`: skips `_show_frame()` while `self.session.is_
    recording`.
  - `_tick()`: reschedules via `root.after_idle(self._tick)` while
    recording, `root.after(PREVIEW_POLL_MS, self._tick)` otherwise.
- `core/recorder.py::encode_frames_with_audio`, `core/playback.py::
  extract_audio`: both `subprocess.run(...)` calls now pass
  `creationflags=subprocess.CREATE_NO_WINDOW`.

`[T-089-01]`, `[T-089-02]` (code portion) verified this session; full
test suite passes.
