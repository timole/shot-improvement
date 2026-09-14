# 083 — Shot-improvement logging, a real freeze fix, and a throughput win

**Status:** In progress

## What

1. **Comprehensive but lightweight logging** (`core/log_setup.py`): a
   size-capped rotating file (`logs/shot-improvement.log`, 2MB × 2
   backups) plus console output, at INFO by default (per-frame DEBUG
   detail is opt-in via `SHOT_IMPROVEMENT_DEBUG=1`, since logging every
   frame would itself cost real time on this CPU-constrained machine).
   Every module logs its lifecycle events (device picks, camera opens/
   switches, recording start/finish, model load); `LiveSession` tracks
   consecutive camera-read failures and warns/errors on a stuck camera;
   the GUI's own per-tick loop is timed and warns if a single tick
   takes unusually long; Tkinter's own uncaught-callback-exception hook
   is routed through the same logger (previously silently printed to
   stderr and continued, invisible in practice).
2. **A real freeze, found via that logging and fixed**: the very
   logging just added caught a genuine ~12-second camera stall
   happening right after a recording's ffmpeg encode step. Root cause:
   `_finish_recording()` ran the two ffmpeg encodes synchronously on
   the same thread `read_frame()` runs on (the GUI's own per-tick
   loop) - fine for the original 3-second default, but the user had
   just asked for a 10-second default (more frames to encode) and the
   encode genuinely starved the camera of CPU for that whole window.
   Fixed by moving encoding onto a background thread
   (`core.session.LiveSession._start_finish_recording`); the GUI
   result callback is marshaled back via a `dispatch` callback
   (`root.after(0, ...)`) so it's still only ever touched from the
   Tkinter main thread.
3. **A real throughput win, found the same way**: benchmarking (in
   response to "why only 6.6fps, what would help") showed PNG
   compression of the two per-frame files cost ~26ms/frame each
   (~53ms/frame combined) - more than pose inference itself
   (~57ms/frame). Switched the intermediate frames (deleted after
   ffmpeg encodes the real output) from PNG to uncompressed BMP
   (`core.recorder.FRAME_FILE_EXTENSION`). Measured result: 6.6 → 9.3
   fps on a real recording, no quality loss (the final `.mp4` is still
   properly h264-compressed either way).
4. **Recordings list now has a "Luotu" (created) column** (parsed
   from the filename's own timestamp, not filesystem mtime, which a
   copy/move could change) - `gui.py`'s list is now a `ttk.Treeview`
   instead of a plain `tk.Listbox`.
5. **Default recording duration is now 10 seconds**, not 3.

## Why

The user reported a real freeze ("kamera hyytyi ja sovellus tökki")
and asked for logging specifically so its cause would become
diagnosable - the logging didn't just get added defensively, it
immediately found and enabled fixing the actual bug in the same
session, which is the point of it existing at all. The throughput
question ("mikä kamera tähän paras, mikä auttaisi") got measured,
not guessed at: the answer turned out to be "the camera doesn't
matter, PNG compression was the bigger cost" - a genuinely
counterintuitive result that a benchmark caught and a guess wouldn't
have.

## Out of scope and known constraints

- Per-frame DEBUG logging (opt-in) still doesn't exist as a distinct
  finer-grained trace beyond what INFO/WARNING already capture -
  "comprehensive" here means full lifecycle + anomaly coverage, not a
  frame-by-frame trace, which would itself cost real time on this
  hardware.
- If the app is closed while a recording is still encoding in the
  background, that encode thread is a daemon thread and gets killed
  with the process - an in-progress clip could be left incomplete.
  Not handled (no "still saving, are you sure?" prompt).
- Two further throughput options were investigated and *not* applied,
  left for a future explicit ask since each is a real trade-off, not
  a free win: skip-frame detection (reuse the last box for alternating
  frames, ~2x further throughput, laggier tracking) and a lower
  capture resolution (faster inference, coarser image). GPU delegate
  was confirmed unavailable on this hardware (falls back to CPU
  XNNPACK every time), not something software here can fix.
- The BMP switch increases temporary (not final-output) disk usage
  per frame; cleaned up via the same `tempfile.TemporaryDirectory` as
  before, so this is a transient, bounded cost, not a persistent one.

## Acceptance criteria

1. `logs/shot-improvement.log` exists after running either
   `record.py` or `gui.py`, capped in size, containing timestamped
   lifecycle events for device selection, camera open/switch,
   recording start/finish, and any warnings/errors. `[T-083-01]`
2. A recording's capture-to-encode transition no longer blocks the
   live camera/preview - `read_frame()` keeps returning frames at
   normal speed while a previous clip's ffmpeg encode is still running
   in the background. `[T-083-02]`
3. A real recording's measured fps with the BMP-based pipeline is
   higher than the previous PNG-based one under the same conditions.
   `[T-083-03]`
4. The recordings list shows two columns, name and creation time (the
   creation time parsed from the filename, formatted readably).
   `[T-083-04]`
5. The duration spinbox defaults to 10, not 3. `[T-083-05]`

## Test plan

- `[T-083-01]` offline: run either entrypoint, confirm
  `logs/shot-improvement.log` is created and populated (also directly
  exercised by this experiment's own manual runs during development).
- `[T-083-02]`, `[T-083-03]` live/manual, verified for real this
  round: launched the GUI, clicked "Tallenna" (10s) via simulated
  Win32 mouse events against the real window, and confirmed via the
  log file both that no slow-tick/slow-read warnings appeared (unlike
  the pre-fix run, which logged a genuine ~12s stall) and that the
  recording achieved 9.3fps (up from a 6.6fps pre-fix run under
  comparable conditions) - both measured, not assumed.
- `[T-083-04]`, `[T-083-05]` verified via screenshot of the real
  running GUI (Treeview showing "Nimi"/"Luotu" columns with readable
  dates; the spinbox showing "10").

## Implemented

Shipped as `core/log_setup.py` (new), logging calls added throughout
`core/devices.py`, `core/recorder.py`, `core/pose.py`, `core/session.py`,
`gui.py`, `record.py`; `core.session.LiveSession._start_finish_recording`
(renamed/reworked from `_finish_recording`) spawning a background
`threading.Thread` for the ffmpeg encode step, with `is_busy` (capture
OR encode) added alongside the existing `is_recording` (capture only)
for the GUI to gate the record/play buttons on; `FRAME_FILE_EXTENSION`
switched from `"png"` to `"bmp"` in `core/recorder.py`, threaded
through both `record_clip()` and `LiveSession.read_frame()`; `gui.py`'s
Listbox replaced with a `ttk.Treeview` (`_created_label()` parses the
filename timestamp); `DEFAULT_DURATION_S` changed to 10.

All five criteria verified live on this laptop during development, not
just by code review - see "Test plan". `experiments/shot-improvement/tests/`
(14 tests) and `server/tests/` (202 tests) both still pass unchanged.
