# 106 — Shot-improvement: browse the recognized shots right after recording

## What

The moment a recording finishes, the GUI's preview is replaced by the
first detected shot's image (camera frame + hand boxes + km/h, now with
a spectrogram strip underneath marking that shot's instant and every
shot/hit pair's travel-time line) instead of staying on the live camera
feed while the slow background pipeline runs. A list of every detected
shot appears below the preview, one row per shot with its km/h; clicking
a row shows that shot's image. This works identically for a normal
recording and for "Vain nauhoitus" (recording-only) mode - the shot list
there stays fast (no video encoding at all) since it only needs the
audio and the handful of raw frames nearest each shot.

## Why

The user's whole reason for recording is the shot analysis (how many
shots, how fast), but until now that analysis only existed as loose
`shot-improvement-<ts>-shot-NN.jpg` files in `recordings/` that nothing
in the app ever showed - the GUI kept displaying the live camera feed,
unrelated to what was just recorded, for as long as the (multi-minute,
on this hardware) background pipeline took.

## Design

**`core/compose.py`**: `save_shot_images()` now returns `list[ShotImage]`
(`index`, `path`, `time_s`, `speed_kmh`) instead of bare `list[Path]`, and
each saved jpg stacks the existing camera-frame content (hand boxes +
km/h label) on top of a new `SHOT_SPECTROGRAM_HEIGHT=200`-tall
spectrogram strip - built once per call (all shot images in one clip
share the same strip content, since every pair's yellow line + km/h is
the same regardless of which shot is being viewed), then copied per shot
with a red playhead (`core.spectrogram.PLAYHEAD_COLOR_BGR`) marking that
shot's own instant, reusing `draw_pair_annotations`/`_x_for_time` already
added for the annotated video (spec 098).

**`core/session.py`**: `LiveSession.start_recording()` gained
`on_shots_ready(shots)`, fired well before the mp4 encodes:

- Normal recording (`_finish_immediate_recording`): shot images now run
  right after `audio.wav` is written, BEFORE both the raw and annotated
  ffmpeg encodes - a deliberate reordering, since this is the fast,
  useful result and previously ran dead last (after the annotated
  encode), which was pointless once anything needed the result promptly.
  This delays `on_raw_ready` (and the record button's re-enable) by the
  shot-image step itself (one pose-model load + a handful of `imread`s,
  roughly 1-2s) - accepted, since the user explicitly wants the analysis
  view immediately.
- "Vain nauhoitus" (`_finish_deferred_recording`): gained the SAME
  shot-image step, run against the just-written `pending/<ts>/raw/`
  frames and the in-memory audio buffer, right after `write_meta()`.
  Still no mp4, no `compose_annotated_frames` call - only claps
  detection + a few frame reads, which is what keeps this mode fluent.
  A failure in this step is caught separately from the pending-save
  itself, so a shot-image bug can never make a successfully-captured
  recording report as "failed" (the footage is safe in `pending/`
  either way).

`RecordingResult` is unchanged - `on_shots_ready` is a separate callback,
independent of (and firing well before) `on_done`.

**`gui.py`**: a third mode, `"shots"`, alongside `"live"`/`"playback"`.
`_on_shots_ready()` fills a new `Treeview` (`shots_tree`, one row per
shot, "Laukaus N" / "NN km/h") packed below the video preview
(`center_frame`, under `preview_label`) and shows shot 1;
`on_shot_selected()` shows whichever row is clicked. `_tick()` leaves the
displayed image alone while in shots mode (same idea as playback while
paused). `_exit_shots_mode()` (a "Takaisin livekuvaan" button, and
automatically when a new recording starts or a saved clip is opened for
playback) restores the live feed and the normal 640x360 display size -
shot images are taller (frame + spectrogram strip), so `_show_shot()`
temporarily widens `self._display_size` to keep the aspect ratio
undistorted.

## Test plan

- `venv\Scripts\pytest tests\` - 100 passed. Extended
  `tests/test_compose.py`'s existing `save_shot_images` tests for the new
  `ShotImage` return type and the taller (frame + strip) composite;
  the 4 `composite_into` pixel-identity oracle tests are untouched.
- Real GUI recording, normal mode, two claps a few seconds apart: preview
  flipped to the first shot's image (frame + hand boxes + km/h + strip
  with a marker + yellow pair line) well before the annotated encode
  finished; the list showed one row with a plausible speed; clicking it
  re-showed the same image.
- Real GUI recording, "Vain nauhoitus" ticked: same shot list appeared,
  no new mp4 was written, the recording stayed queued in `pending/`;
  "Käsittele odottavat" still processed it into both mp4s afterward,
  overwriting the same shot jpgs with identical output.
- Started a new recording directly from shots mode, and opened a saved
  clip for playback from shots mode - both correctly left shots mode and
  behaved normally.
