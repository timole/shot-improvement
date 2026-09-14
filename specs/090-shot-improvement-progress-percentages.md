# 090 — Shot-improvement: progress percentages for slow post-capture steps

## What

The GUI's status line now shows a live percentage for each of the
slow steps that run after a recording finishes: pose annotation
("Tunnistetaan käsien asentoja: N %"), the spectrogram pass
("Lisätään spektrogrammi: N %"), each of the two ffmpeg encodes
("Tallennetaan levylle (raaka)/(-): N %"), and the cloud upload
("...Synkronoidaan verkkoon: N %") - previously each of these was one
static line with no indication of how far along it was, on steps that
can individually take several seconds.

New `on_progress(done, total)` callback parameter (or, at the ffmpeg/
GCS layer, `on_progress(bytes/frames done, total)`) threaded through:

- `core/pose.py::annotate_frames_dir` - called after each frame.
- `core/spectrogram.py::add_spectrograms_to_frames` - called after
  each frame.
- `core/recorder.py::encode_frames_with_audio` - now runs ffmpeg with
  `-progress pipe:1 -nostats -stats_period 0.1` instead of a plain
  `subprocess.run(capture_output=True)`, parsing the `frame=N` lines
  ffmpeg writes to stdout as they arrive.
- `core/cloud_sync.py::upload` - now does a real chunked resumable
  upload (256 KiB chunks - GCS's minimum) when `on_progress` is given,
  instead of the previous single-call `blob.upload_from_filename`,
  reporting `bytes_uploaded`/`total_bytes` after each chunk.

`core/recorder.py::record_clip` (CLI path) and `core/session.py::
LiveSession.start_recording`/`_start_finish_recording` (GUI path) both
gained a higher-level `on_progress(stage_label, fraction)` parameter
that wraps each of the above into one consistent callback - `record.py`
prints it in place on one line; `gui.py` sets `status_var` from it
(dispatched to the Tkinter thread, same convention as `on_done`).
`core/cloud_sync.py::SyncWorker.upload_recording` gained the same,
dispatched the same way.

## Why

The user asked for this directly: "näytä prosenttilukema, että paljonko
valmiina" (show a percentage of how much is done) for saving to disk,
and the same for annotating and cloud syncing - these steps are slow
enough on this hardware (pose inference ~57ms/frame; ffmpeg encoding
100s of 1280x720 frames; upload speed depending on home connection)
that a single static "working..." line for several seconds at a time
is a real UX gap.

## Out of scope and known constraints

- ffmpeg's `-progress` stats are emitted on its own internal cadence
  (`-stats_period 0.1` asks for ~100ms, not guaranteed exact) - fine
  for clips a few seconds to ~10s long, which is this app's normal
  range.
- The chunked resumable upload path in `core/cloud_sync.py::upload`
  uses `Blob._initiate_resumable_upload`, a private
  `google-cloud-storage` API (no public equivalent exposes the
  in-progress `ResumableUpload`/transport pair needed to observe
  `bytes_uploaded` between chunks) - it's exactly what `Blob.
  upload_from_filename` does internally, so it's not doing anything
  the library wouldn't already do, but it is a private-API dependency,
  not a stable public contract.
- Not verified live end-to-end against real GCS - the laptop currently
  has no Application Default Credentials configured
  (`google.auth.exceptions.DefaultCredentialsError` on every upload
  attempt, confirmed in logs and via `gcloud auth application-default
  print-access-token`), a separate, pre-existing problem unrelated to
  this spec. The upload progress code path is covered by the same
  reasoning as spec 088/089's ffmpeg fixes (verified via the
  `google-resumable-media` API directly, `inspect.signature`-checked
  against the installed version) but not by a real upload this
  session.
- Discovered while testing this spec, unrelated to it: a real ffmpeg
  subprocess deadlock existed in `encode_frames_with_audio` the moment
  `-progress pipe:1` was introduced and stdout/stderr were both piped
  - reading only stdout in a loop while ffmpeg's default-verbosity
  stderr output (banner + libx264 param dump) filled its OS pipe
  buffer left both sides blocked forever. Fixed with a dedicated
  stderr-draining thread (see "Implemented"); a live CLI recording
  hung indefinitely before this fix and completed normally after.
- Also observed, unrelated to this spec and not fixed here: partway
  through this session's testing, the Logitech C922 stopped appearing
  in Windows' own device enumeration at all (`core.devices.
  list_video_devices()` returned only `["USB Camera", "OBS Virtual
  Camera"]`, confirmed twice a few seconds apart) - a real, currently-
  present hardware/driver dropout, not a code issue; recording
  correctly fell back to the weaker camera per existing fallback
  logic rather than failing outright. Matches this machine's known
  intermittent camera issues (spec 086).

## Acceptance criteria

1. A CLI recording (`record.py`) prints an in-place, incrementing
   percentage for each of: annotating, spectrogram, and both ffmpeg
   encodes, reaching 100% before moving to the next stage.
   `[T-090-01]`
2. `encode_frames_with_audio` does not deadlock when both progress
   parsing (stdout) and default-verbosity logging (stderr) are active
   at once. `[T-090-02]`
3. `add_spectrograms_to_frames` and `annotate_frames_dir` both call
   `on_progress(done, total)` with `done` reaching `total` exactly
   once processing completes. `[T-090-03]`
4. `core/cloud_sync.py::upload`, given `on_progress`, uploads in more
   than one chunk for a file above `UPLOAD_CHUNK_SIZE` and calls
   `on_progress` with strictly increasing `bytes_uploaded` ending at
   the file's real size. `[T-090-04]` (not verified live - see "Out of
   scope")

## Test plan

- `[T-090-01]`, `[T-090-02]` live: `record.py 4` end to end. First
  attempt (before the stderr-drain fix) hung indefinitely with ffmpeg
  and its parent `python.exe` both still alive per `Get-CimInstance`
  minutes later - a real deadlock, not a slow encode. After the fix,
  the same command completed in a few seconds with percentages
  printing 0%->100% for each of the four stages in order, then wrote
  both output files successfully.
- `[T-090-03]` code: both functions' loops call `on_progress(i + 1,
  total)` (or `frame_count`) unconditionally each iteration, including
  on a corrupt/unreadable frame (`cv2.imread` returning `None`) - a
  bad frame still counts toward progress rather than silently
  stalling it.
- `[T-090-04]` code + API inspection only (see "Out of scope"):
  `google.resumable_media.requests.ResumableUpload`'s constructor,
  `.initiate`, `.transmit_next_chunk`, `.finished`, and
  `.bytes_uploaded` were confirmed via `inspect.signature`/`dir()`
  against the installed `google-resumable-media` version to match how
  they're used in `core/cloud_sync.py::upload`.
- `pytest`: full suite (35 tests) passes unchanged.

## Implemented

- `core/pose.py::annotate_frames_dir`: new `on_progress` parameter,
  called after every frame (including unreadable ones).
- `core/spectrogram.py::add_spectrograms_to_frames`: same.
- `core/recorder.py::encode_frames_with_audio`: rewritten from
  `subprocess.run(capture_output=True)` to `subprocess.Popen` with
  `-progress pipe:1 -nostats -stats_period 0.1`, a dedicated
  stderr-draining thread (the deadlock fix - see "Out of scope"), and
  a new required `frame_count` parameter (needed to turn ffmpeg's own
  `frame=N` into a fraction).
- `core/recorder.py::record_clip`: new `on_progress(stage, fraction)`
  parameter, wraps each of the four calls above.
- `core/session.py::LiveSession.start_recording`/
  `_start_finish_recording`: same, dispatched to the GUI thread.
- `core/cloud_sync.py::upload`: new `on_progress` parameter; chunked
  resumable upload path via `Blob._initiate_resumable_upload` (see
  "Out of scope" for the private-API caveat) when given, unchanged
  single-call path when not.
- `core/cloud_sync.py::SyncWorker.upload_recording`/`_run`: threads
  `on_progress` through the sync queue, dispatched to the GUI thread.
- `record.py`: prints `on_progress` in place on one line.
- `gui.py`: `_on_recording_progress`/`on_sync_progress` set
  `status_var` from the respective callback.

`[T-090-01]`-`[T-090-03]` verified live/via code this session.
`[T-090-04]` verified via API inspection only, not a real upload (no
GCP Application Default Credentials on this machine - separate,
pre-existing issue, not fixed by this spec).
