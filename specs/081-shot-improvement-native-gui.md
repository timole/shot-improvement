# 081 — Shot-improvement native GUI

**Status:** In progress

## What

A Tkinter GUI (`experiments/shot-improvement/gui.py`, same dedicated
venv as spec 080's CLI) replacing/adding to the CLI entrypoint:

- On launch, opens the camera/mic once (`core/session.LiveSession`)
  and shows a continuously palm-box-annotated live preview - not just
  during a recording, unlike the CLI.
- A "Nauhoita 3 sekuntia" button (disabled until the camera is ready)
  records a clip using the *same already-open* camera - no second
  `getUserMedia`/`VideoCapture`-style device negotiation.
- A list below shows every saved recording (newest first). Double-
  click or "Toista" opens the selected file with the OS's default
  video player (`os.startfile` - proper audio/video sync for free,
  no custom player to build). "Poista" deletes the selected file
  after a confirmation dialog.

`core/recorder.py`'s CLI-facing helpers (`pick_video_device`,
`pick_audio_device`, `encode_frames_with_audio`,
`timestamp_for_filename`) were kept and reused rather than duplicated;
the two now-public names were `_encode`/`_timestamp_for_filename`
before this spec, private since nothing outside `recorder.py` needed
them yet.

## Why

The CLI (spec 080) works but gives no feedback before/during a
recording beyond terminal text - no way to see the camera is even
pointed at the right thing, or to review/manage past recordings
without leaving the terminal. A native GUI closes that loop while
keeping the same "no browser, low memory" premise: Tkinter ships with
Python (no extra runtime), and playback is delegated to the OS's own
video player instead of building one - both choices avoid adding
weight on a ~3.9GB-RAM machine.

A single persistent `LiveSession` (one open camera, one `PoseDetector`,
for the GUI's whole lifetime) was necessary, not just a convenience:
`core.recorder.record_clip()` opens and closes the camera per call,
but DirectShow doesn't reliably allow reopening a camera that's
already open elsewhere - the live preview and the recording button
have to share one already-open device instead of each managing their
own.

## Out of scope and known constraints

- `_finish_recording()` (ffmpeg mux + `sd.wait()`) runs synchronously
  inside the same Tkinter `after()`-driven frame loop that also drives
  the live preview - the UI briefly pauses updating during the ~1s
  encode step at the end of each recording. Fine for a 3s/~20-30-frame
  clip; would need moving off the main thread for longer recordings.
- `os.startfile()` is Windows-only, consistent with the rest of this
  experiment (DirectShow/pygrabber are already Windows-specific).
- No visual "recording in progress" indicator beyond the status text
  and the button disabling - no countdown, no on-preview overlay.
- Continuous pose inference (not just during the 3s window) means the
  CPU cost that CLI users only pay for 3 seconds is now paid for as
  long as the GUI window is open - a deliberate tradeoff for the
  "annotated from launch" requirement.

## Acceptance criteria

1. Launching `gui.py` shows a live camera preview with palm boxes
   drawn continuously, before any recording starts. `[T-081-01]`
2. The "Nauhoita 3 sekuntia" button is disabled until the camera is
   ready, and clicking it records a clip without reopening the camera
   (no second permission/device negotiation). `[T-081-02]`
3. After recording, the status area shows a result message and the
   recordings list refreshes with the new clip at the top.
   `[T-081-03]`
4. Double-clicking (or selecting + "Toista") a listed recording opens
   it in the OS's default video player. `[T-081-04]`
5. Selecting a recording and clicking "Poista" deletes it (after
   confirmation) and removes it from the list. `[T-081-05]`

## Test plan

- `[T-081-01]`–`[T-081-03]` live/manual, verified by the author during
  development (see "Implemented" - actually exercised through the real
  GUI, not just code review, via simulated Win32 mouse clicks +
  screenshots).
- `[T-081-04]`, `[T-081-05]` code-reviewed but not click-tested live
  (see "Implemented") - both are a few lines of standard
  `os.startfile`/`Path.unlink` + `tk.messagebox`, considered low-risk.
- No automated test exercises the GUI itself (no camera/display in
  CI); `core/session.py`'s only new pure logic
  (`RecordingResult`/timestamp reuse) is already covered indirectly by
  spec 080's existing unit tests on the functions it reuses.

## Implemented

Shipped as `experiments/shot-improvement/gui.py` +
`core/session.py`. `core/recorder.py`'s `_encode`/
`_timestamp_for_filename` were renamed to `encode_frames_with_audio`/
`timestamp_for_filename` (now imported by `session.py` too);
`tests/test_recorder.py` updated to match.

Verified live end-to-end on this laptop: launched the GUI, confirmed
via screenshot that the preview/button/list render correctly, then
actually clicked "Nauhoita 3 sekuntia" through simulated Win32 mouse
events (`SetCursorPos`/`mouse_event` against the real window handle,
DPI-aware) rather than only inspecting the code - the first two click
attempts landed in the wrong place before the coordinates were
calibrated against an actual screenshot; the corrected click produced
two new files on disk (confirmed via directory listing) and the UI
itself updated to "Tallennettu (27 kuvaa, 8.8 fps)." with the new
recording at the top of the list (confirmed via a follow-up
screenshot). `[T-081-01]`–`[T-081-03]` verified this way.
`[T-081-04]`/`[T-081-05]` (play/delete) were not click-tested the same
way - reviewed in code only, to avoid the risk of a mis-clicked
automated delete on a real recording.
