# 093 — Shot-improvement: "Vain nauhoitus" (recording-only, deferred processing)

## What

A new GUI checkbox, **"Vain nauhoitus (käsittele myöhemmin)"** (recording
only - process later), next to the record button. When checked, a
recording captures exactly as normal but **no processing runs
afterward at all** - no pose annotation, no spectrogram, no ffmpeg
encoding, no cloud sync. Raw frames + audio are saved to a new durable
`pending/<timestamp>/` directory instead of the usual ephemeral temp
dir.

A new **"Käsittele odottavat"** (process pending) button, with a live
"Odottaa käsittelyä: N" (N pending) count next to it, processes the
whole queue - oldest first - turning each pending capture into the
usual raw + annotated mp4 pair (in `recordings/`, same as always) and
deleting its `pending/` entry once both files exist. Processing runs
in the background at the same lowered OS thread priority as normal
post-capture processing (spec 091), so **a new recording still always
takes priority** even while a pending batch is being worked through.

## Why

The user asked for this directly: "the computer gets slow when
recordings are processed and new recordings cannot be done then." Spec
091 already prioritizes a *new* recording's own capture over an
*older* recording's still-running background processing under
contention (via `lower_current_thread_priority()`), but processing
still competes for the same weak 4-core 1.1GHz CPU - it doesn't fully
disappear, just yields when it can. This feature gives the user a way
to skip that contention entirely rather than just deprioritize it:
capture a whole burst of clips back to back with nothing but a camera
read + one disk write per frame (the same minimal capture-loop cost
spec 086 already established), then process everything at once,
whenever the machine is free to spend a few minutes on it.

## Design

- **`core/pending.py`** (new): `PENDING_DIR` (gitignored, alongside
  `recordings/`), `PendingRecording` (a plain dataclass read from each
  capture's `meta.json`), `list_pending()` (oldest first, skips any
  entry with a missing/malformed `meta.json` rather than raising),
  `write_meta()`, `delete_pending()`, and
  `process_pending_recording()` - the actual encode -> compose (spec
  092) -> encode pipeline, run on its own background thread, reading
  audio back from the WAV file it was saved as (a lossless round-trip
  for int16 PCM, not a correctness compromise) rather than needing an
  in-memory array to still be around.
- **`core/session.py`**: `LiveSession.start_recording()` gained
  `defer_processing: bool` and `on_deferred_saved(ok)`. When
  `defer_processing=True`, capture writes straight into
  `PENDING_DIR/<timestamp>/raw/` (no temp dir - the whole point is
  that this data must survive past the call), and
  `_start_finish_recording()` branches to a new, much smaller
  `_finish_deferred_recording()` (write `audio.wav` + `meta.json`, done)
  instead of the existing `_finish_immediate_recording()` (renamed from
  the previous inline worker, unchanged otherwise - still encode raw ->
  `on_raw_ready` -> compose -> encode annotated, exactly as before).
  Both share the same `_encoding_count`/`is_busy` bookkeeping and
  `lower_current_thread_priority()` pattern.
- **`gui.py`**: the checkbox, the pending count label + "Käsittele
  odottavat" button, `refresh_pending()`, `_on_deferred_saved()`, and a
  small self-continuing queue drain (`on_process_pending_click` ->
  `_process_next_pending`, processing one item at a time, refreshing
  the count after each, stopping when the queue is empty). A shared
  `_handle_processed_result()` was factored out of `_on_recording_done`
  so a processed *pending* item's success path (refresh the list,
  upload to the cloud, report status) is identical to a normal
  recording's - **deliberately without touching the record button's
  state**, which only a real in-progress recording should ever control.
- Processing is **not gated on the camera being open at all** -
  `process_pending_recording()` never touches `LiveSession`/the camera,
  so a backlog from a previous session (or one left over after a
  camera failure) can still be processed. `refresh_pending()` is called
  once in `App.__init__`, before `_init_session()` even runs.

## Out of scope

- No per-item UI (delete one pending item without processing it,
  inspect a pending item's frame count before committing to the whole
  queue) - "Käsittele odottavat" always processes the full queue,
  oldest first. A discard/delete-without-processing action was
  considered but not requested; `core.pending.delete_pending()` exists
  and a button could call it directly if wanted later.
- No progress indicator for the QUEUE as a whole beyond
  "(index+1/total)" in the status line - no separate progress bar.
- The CLI (`record.py`/`core.recorder.record_clip`) is untouched - it
  is already a single synchronous one-shot command with nothing else
  running concurrently to contend with, so deferred processing has no
  purpose there.
- Shutdown mid-batch (closing the GUI while `process_pending_recording`
  is running) is not specially handled - same pre-existing,
  accepted risk as a normal recording's own background encode not
  being waited on at `on_close()` either. Not a new regression.

## Test plan

- `tests/test_pending.py` (new, 13 tests): `write_meta`/`list_pending`
  round-trip, ordering (oldest first), skipping a directory with no or
  malformed `meta.json`, skipping non-directory entries,
  `delete_pending` (including on an already-missing directory),
  `PendingRecording.created_label()` (valid + fallback). All pure
  logic, no camera/ffmpeg/MediaPipe - matches this repo's existing test
  style (nothing exercises capture or the real processing pipeline in
  `tests/`).
- Full suite: `venv\Scripts\pytest tests\` - **52 passed** (39
  pre-existing + 13 new), no regressions.
- Live, end-to-end, against the real camera+mic this session (a
  standalone verification script driving `LiveSession` and
  `core.pending` the same way `gui.py` does, not part of the permanent
  test suite): a 2s `defer_processing=True` recording correctly saved
  raw BMPs + `audio.wav` + `meta.json` to `pending/<timestamp>/` with
  **no ffmpeg/pose-detection activity at all** during or after
  capture (confirmed via the log); `process_pending_recording()` then
  correctly produced both mp4s (verified non-empty, correct filenames)
  and deleted the pending source directory; the pending count went
  0 -> 1 -> 0 as expected.
- Not verified this session: the actual GUI checkbox/button click path
  (requires manual interaction) - the underlying `LiveSession`/
  `core.pending` API both routes call was verified directly instead,
  and `gui.py` was confirmed to import and compile cleanly.

## Implemented

- `core/pending.py` (new): `PendingRecording`, `write_meta`,
  `list_pending`, `delete_pending`, `process_pending_recording`.
- `core/session.py`: `start_recording()` gained `defer_processing`/
  `on_deferred_saved`; `_start_finish_recording()` now dispatches to
  `_finish_immediate_recording()` (the pre-existing worker, extracted
  unchanged) or the new `_finish_deferred_recording()`.
- `gui.py`: "Vain nauhoitus" checkbox, pending count label +
  "Käsittele odottavat" button, `refresh_pending()`,
  `_on_deferred_saved()`, `on_process_pending_click()`,
  `_process_next_pending()`, `_handle_processed_result()` (factored
  out of `_on_recording_done()`).
- `.gitignore`: added `pending/`.
- `tests/test_pending.py` (new, 13 tests).
