# 105 — Shot-improvement: temporarily keep each recording's temp dir

## What

A normal (non-deferred) recording's temp directory - raw/annotated
frame BMPs + `audio.wav` - is no longer deleted the instant processing
finishes. Its path is shown in the GUI (a new label under the status
line) once the recording completes, so it can be inspected by hand.

## Why

The user asked where a recording's raw data goes mid-processing, then
asked to keep it around to look at, with an explicit note that a
later prompt will ask for automatic cleanup to come back.

## Design

`core.session.KEEP_TEMP_DIR_FOR_INSPECTION` (new module constant,
`True`) gates the behavior in exactly two places:

- `LiveSession.start_recording()`: when `True`, the recording's temp
  directory is created with `tempfile.mkdtemp()` instead of
  `tempfile.TemporaryDirectory()` - the latter has a `weakref.
  finalize`-based finalizer that deletes the directory when the
  object itself is garbage-collected, which would undo "keep it"
  even if the explicit `.cleanup()` call were simply skipped;
  `mkdtemp()` has no such finalizer, so the directory genuinely stays
  until something removes it.
- `_finish_immediate_recording`'s worker(): the `tmp_dir.cleanup()`
  call in its `finally` block is now guarded on `tmp_dir is not None`
  (it's `None` whenever `mkdtemp()` was used, since there's no
  `TemporaryDirectory` object to call `.cleanup()` on).

`RecordingResult` gained an optional `tmp_dir_path` field, only
populated when `KEEP_TEMP_DIR_FOR_INSPECTION` is on (never advertising
a path that's about to be deleted otherwise). `gui.py` shows it in a
new label (`tmp_dir_var`/`tmp_dir_label`) under the status line,
updated in `_handle_processed_result` - the same place both a normal
recording's and a processed-pending-item's result flow through.

**Deliberately not touched**: the CLI path (`core.recorder.record_clip`,
always uses a plain `with tempfile.TemporaryDirectory()`) and the
deferred "Vain nauhoitus" queue's own processing
(`core.pending.process_pending_recording`, has its own separate,
always-cleaned-up temp dir for the annotated-frames pass) - the user's
question was specifically about a normal GUI recording.

**Explicitly temporary**: this is scoped to be reverted on request -
flip `KEEP_TEMP_DIR_FOR_INSPECTION` back to `False` (no other code
changes needed; the `mkdtemp()`/`TemporaryDirectory()` branch and the
guarded cleanup call both already handle the `False` case correctly)
when asked to restore automatic cleanup.

## Test plan

- `venv\Scripts\pytest tests\` - 100 passed, no test changes needed
  (no existing test exercised temp-dir cleanup timing).
- Not yet verified this session: a real GUI recording, confirming the
  temp folder path shows in the new label and the folder genuinely
  survives on disk afterward - planned as the next step.
