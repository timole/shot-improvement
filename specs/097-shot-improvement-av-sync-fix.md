# 097 — Shot-improvement: fix the audio/video sync bug

## What

Recordings' video no longer drifts out of sync with their audio.
Applies to every recording made from now on (CLI, GUI, and the
"Vain nauhoitus" deferred-processing queue alike); the 39 clips
already in `recordings/` before this fix each got a best-effort
`-fixed.mp4` twin (see "Existing clips" below) and those were synced
to Azure Blob Storage.

## Why

The user's report: "audio comes earlier than the picture", asking for
a real recorded test (a beep, captured through the Logitech mic) to
find where the timeshift actually occurs before fixing it.

## Diagnosis

A 5s test recording, run through the exact production capture/encode
functions (`core.recorder.open_camera`/`encode_frames_with_audio`)
with a `winsound.Beep()` triggered at a known `time.monotonic()`
instant and every frame's own real capture timestamp logged
separately, found:

- **Frame capture timing is highly non-uniform on this hardware**: 149
  frames over 5.0s (mean interval 34ms) but with real gaps ranging
  0-375ms - a camera stall (DirectShow/USB, under load) followed by a
  burst of near-zero-gap frames draining a buffer, not steady 30fps.
- **`encode_frames_with_audio` encoded that sequence assuming even
  spacing** - a single `-framerate <frame_count/elapsed_seconds>`
  passed to ffmpeg's image2 demuxer, which has no way to know real
  per-frame timing and just divides total duration evenly across
  every frame.
- **Measured impact**: extracting the output frame shown at the beep's
  known real-world instant and diffing it (pixel-by-pixel) against the
  raw captured frames found the wrong frame on screen by **781ms** -
  the frame actually visible at that moment in the video was really
  captured more than three quarters of a second away from when the
  beep played. The audio track (a continuously-sampled WAV, not
  frame-quantized) carries no equivalent distortion, so this
  timestamp-assignment bug in the VIDEO side is what produced the
  audio/video desync - not an audio pipeline delay.

## Fix

`core.recorder.encode_frames_with_audio` now takes each frame's real
`time.monotonic()`-based capture timestamp (`frame_times`, newly
tracked by both capture loops -
`core.recorder.record_clip` and `core.session.LiveSession`) and builds
an ffmpeg **concat demuxer** (`ffconcat`) input instead of a
constant-`-framerate` image2 sequence: each frame gets its own
measured on-screen duration (the real gap to the next frame's capture
time), with the documented last-file-duration-ignored quirk worked
around by repeating the final frame as a trailing entry. `-fps_mode
cfr -r <fps>` then resamples that correctly-timed sequence onto a
normal constant-rate output (steady, seekable playback in a browser)
by duplicating/dropping frames to match their real timestamps - not by
reverting to an even-spacing assumption.

`core.compose.compose_annotated_frames`'s spectrogram playhead had the
same bug one level up (`x_fraction = frame_index / total_frames`,
assuming frames land evenly across the clip) - now uses each frame's
real timestamp against the audio's own real duration instead. The pose
detector's per-frame clock (`ts_ms`) switched from `i * 1000 / fps` to
the same real timestamps for the same reason, though MediaPipe is far
less sensitive to this.

**Falls back to the old even-spacing behavior** when `frame_times`
isn't given or its length doesn't match `frame_count` - keeps
`encode_frames_with_audio`/`compose_annotated_frames` safe to call from
anywhere that genuinely has no per-frame timing (nothing in this app
does today; a pre-spec-097 `pending/` entry mid-queue across an
upgrade is the one realistic case, handled via
`PendingRecording.frame_times` defaulting to `[]`).

**Measured fix**: the same before/after frame-extraction check that
found a 781ms error found **16ms** (one camera frame's worth of
sampling granularity) after the fix - re-running the concat-demuxer
path against the original test recording's raw frames + real
timestamps.

## Existing clips

The 39 clips already in `recordings/` before this fix have no
per-frame timing to redo the real fix with - their raw frames were
already deleted once encoded (this app never kept them past encoding,
by design - see `core.recorder`'s module docstring on memory use).
`tools/fix_existing_video_sync.py` (new) is a best-effort retrofit: it
shifts each existing clip's audio track later by a constant 1.0s
(`-itsoffset`, stream-copied - lossless, no re-encode), matching the
user's own measured estimate on
`shot-improvement-20260915112625-annotated.mp4` ("audio comes
approximately 1 second early"). This is an **approximation**, not the
real fix - a constant offset can't undo the time-varying distortion
above (a clip's own stall pattern shifts different frames by different
amounts), but it's the best available correction for footage whose
original frame timing is unrecoverable. Output: `<original
stem>-fixed.mp4` next to each original, which is left untouched.

Run once this session; all 39 pairs succeeded and were spot-checked
(valid streams, correct duration). The `-fixed.mp4` files were then
uploaded to Azure Blob Storage only (`core.cloud_sync.upload()` against
just `core.azure_sync.AzureBlobBackend()`, not GCS - matching spec
096's "no GCP dependency" for this service) - Azure's `clips` container
went from 39 to 78 video blobs.

## Test plan

- `venv\Scripts\pytest tests\` - 68 passed (5 new: three for
  `_write_concat_list`'s real-gap/last-frame-repeat/single-frame
  logic, two for `frame_times` round-tripping through
  `write_meta`/`list_pending`, including the pre-spec-097
  empty-default fallback).
- A real 5s recording through `tools/bench_record.py` (production
  `record_clip`, real camera/mic - an unusually severe camera stall
  that session, only 5 frames captured) encoded successfully through
  the new concat-demuxer path with no ffmpeg failure; output duration
  matched the audio exactly.
- The diagnosis's own before/after check (see "Fix" above): re-encoded
  the original test recording's raw frames through both the old and
  new paths and confirmed which real frame appeared at a known
  timestamp in each - 781ms error (old) vs. 16ms (new).
- All 39 existing `recordings/*-fixed.mp4` files verified as valid,
  correctly-durationed mp4s (ffmpeg stream probe) and confirmed present
  in the Azure `clips` container (78 video blobs, up from 39).
- Not verified this session: real end-to-end perceptual confirmation
  from the user that a newly recorded clip (or one of the `-fixed.mp4`
  retrofits) actually looks/sounds in sync when watched.
