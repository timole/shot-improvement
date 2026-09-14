# 091 — Countdown+beep, default mic, prioritized new recording, fullscreen layout

## What

Four independent GUI/behavior changes, all requested together:

1. **Default microphone**: the C922's own built-in mic
   (`PREFERRED_MIC_LABEL = "c922"`) is now the default recording input,
   not the Jabra. Output (playback) device preference is unchanged
   (`PREFERRED_SPEAKER_LABEL = "jabra"`) - split into two separate
   constants in `core/recorder.py` since there's no reason they should
   be forced to match.
2. **3-2-1 countdown before capture**: clicking "Tallenna" now shows
   "Tallennus alkaa: 3..", "2..", "1.." (one second apart, one
   `winsound.Beep()` each) before actually starting the recording and
   showing "Tallennus käynnistyi".
3. **New recording shown immediately, prioritized over old background
   work**: the raw clip now encodes *before* pose annotation (it needs
   no annotation at all), and is handed to a new `on_raw_ready`
   callback the moment it's done - the GUI shows it in the recordings
   list and re-enables "Tallenna" right away, rather than waiting for
   annotation/spectrogram/encode-annotated/cloud-sync to finish. A new
   recording is now allowed to start while an older one's background
   work is still running (previously blocked - see spec 083's original
   reasoning, deliberately overridden here per explicit request), and
   that background work runs at `BELOW_NORMAL_PRIORITY_CLASS`
   (ffmpeg subprocess) / a lowered thread priority (Python pose-
   annotation and cloud-sync threads) so the new recording's live
   capture is preferred by Windows' own scheduler under CPU contention.
4. **Fullscreen layout**: when the window is maximized ("zoomed" is
   Tk's name for it on Windows), the recordings list moves to a fixed-
   width (340px) tall column on the right instead of a short list
   below everything else.

## Why

All four came from the user directly in one message, aimed at making
the recording workflow itself smoother and faster to iterate with:
knowing when capture actually starts (countdown+beep, since the user
is in front of the camera, not the screen), not having to wait out an
older clip's slow background processing before recording the next one,
and using a maximized window's extra space for the list they use to
review clips.

## Out of scope and known constraints

- The `BELOW_NORMAL_PRIORITY_CLASS`/`SetThreadPriority` demotion is
  Windows-only and best-effort (wrapped in try/except, logs a warning
  and continues if it fails) - this app is Windows-only already
  (DirectShow/pygrabber), so no cross-platform fallback was written.
- Multiple background workers (an older clip's annotate/spectrogram/
  encode/sync, a newer clip's same pipeline once *it* finishes
  capturing) can now genuinely run concurrently. `LiveSession.
  is_busy` was changed from a bool to a count (`_encoding_count`) to
  stay correct when more than one is in flight - verified by code
  reading, not a live multi-overlap test (doing that live would need
  two full recording cycles timed precisely against each other).
- Countdown/beep is GUI-only (`gui.py`) - `record.py` (CLI) and
  `core.recorder.record_clip()` are unchanged; the user's request was
  specifically about clicking "Tallenna", the GUI button.
- The fullscreen layout switch is driven by `root.state() == "zoomed"`
  via a `<Configure>` binding - not tested against an actual maximize
  this session (no way to drive that from here); reasoned through
  and code-reviewed instead. See "Test plan".

## Acceptance criteria

1. `pick_audio_device()` picks a name containing "c922" when present,
   not "jabra". `[T-091-01]`
2. Clicking "Tallenna" shows a 3-2-1 countdown (one second apart) with
   a beep on each step, then starts the actual recording. `[T-091-02]`
3. The raw clip appears in the recordings list, and "Tallenna"
   re-enables, before the annotated clip or cloud sync finish.
   `[T-091-03]`
4. Clicking "Tallenna" again while an older clip's background work is
   still running is not blocked. `[T-091-04]`
5. `LiveSession.is_busy` stays `True` until the LAST of possibly
   several concurrent background workers finishes, not whichever
   finishes first. `[T-091-05]`
6. Maximizing the window moves the recordings list to a tall right-
   hand column; restoring it moves the list back below the rest.
   `[T-091-06]` (not verified live - see "Out of scope")

## Test plan

- `[T-091-01]` live: `pick_audio_device()` logged `Picked audio input
  1: 'Mikrofoni (C922 Pro Stream Webc'` on this machine (was `system
  default (no 'jabra' match found)` before this spec).
- `[T-091-02]` code review (not independently clicked this session -
  see limitations) - `_run_countdown` schedules itself via
  `root.after(1000, ...)` three times, calling `_play_beep()` (a
  daemon thread running `winsound.Beep`) each time, before calling
  `session.start_recording(...)`.
- `[T-091-03]`, `[T-091-04]` live: real recordings made by the user
  during this session (both before and after this spec's changes were
  applied - the raw-first encode ordering is a straightforward
  reordering of already-tested `encode_frames_with_audio`/
  `annotate_frames_dir` calls, individually exercised by spec 090's
  live tests) plus code reading of `_start_finish_recording`'s new
  ordering and `on_record_click`'s `is_recording`-only gate (was
  `is_busy`).
- `[T-091-05]` code: `_encoding_count` is incremented in
  `_start_finish_recording` and decremented in `worker()`'s `finally`,
  matching the existing (bool) pattern's placement exactly - two
  concurrent workers each increment then decrement independently, so
  the count only reaches zero once both have.
- `[T-091-06]` not verified live this session (see "Out of scope").
- `pytest`: full suite (35 tests) passes unchanged.

## Implemented

- `core/recorder.py`: `PREFERRED_AUDIO_LABEL` split into
  `PREFERRED_MIC_LABEL`/`PREFERRED_SPEAKER_LABEL`; new
  `lower_current_thread_priority()` (Windows `SetThreadPriority`,
  best-effort); `encode_frames_with_audio`'s ffmpeg `Popen` now also
  passes `BELOW_NORMAL_PRIORITY_CLASS`.
- `core/session.py`: `_encoding` (bool) -> `_encoding_count` (int);
  `start_recording()` gates on `is_recording` (not `is_busy`) and
  accepts a new `on_raw_ready` callback; `_start_finish_recording`'s
  worker encodes the raw clip first, dispatches `on_raw_ready`, then
  continues with annotate/spectrogram/encode-annotated; calls
  `lower_current_thread_priority()` at the top of the worker.
- `core/cloud_sync.py`: `SyncWorker._run`'s thread also calls
  `lower_current_thread_priority()` once at start.
- `gui.py`: `on_record_click`/`_run_countdown`/`_play_beep` implement
  the countdown; `_on_raw_ready` shows the new clip and re-enables the
  button; `_set_status_if_idle` guards status-label updates from an
  older clip's background callbacks against clobbering a newer
  recording's own countdown/capture/progress display; `left_frame`/
  `right_frame` + `_apply_layout`/`_on_root_configure` implement the
  fullscreen layout switch.

`[T-091-01]`, `[T-091-03]`-`[T-091-05]` verified live/via code this
session. `[T-091-02]`, `[T-091-06]` implemented and code-reviewed but
not independently exercised live - flagged for the user to confirm.
