# 108 — Shot-improvement: raw shot playback with no encoding

## What

Spec 107's "▶ Toista raakakuvaa" button built a short ffmpeg-encoded
mp4 on click, then played it through the full saved-recording
transport. It now instead reads and displays the shot's own raw BMP
frames directly in the preview area - no ffmpeg, no temp file, no
audio - starting 2 seconds before the shot through to its paired hit
(or to the recording's end for a trailing unpaired shot). A speed
picker (0.25x / 0.5x / 1.0x / 2.0x) sits next to the play/pause toggle.

## Why

The whole point of "Play" is a fast look at what happened - waiting on
an encode (even a short one) worked against that, and the raw frames
are already sitting on disk with real per-frame timestamps; there was
never a need to turn them into a video file just to look at them once.

## Design

This **replaces** spec 107's approach entirely, not a variant of it.
Removed: `core.compose.build_shot_clip()` and its ffmpeg-encode tests;
`ShotSource.audio_path`/`actual_fps` (no audio, and pacing now comes
from each frame's own real `frame_times` gap, not a flat fps);
`gui.py`'s background-thread build, `ClipPlayer`-based playback
(`_play_raw_shot_clip`/`_restore_shots_mode`/`_shots_playback_active`),
`load_and_play`'s `bypass_busy` parameter (reverted to its spec-106
form), and the per-shot temp-mp4 cache + its `on_close()` cleanup.

`core.compose.select_shot_frame_range(frame_times, shot_t, hit_t) ->
(start_idx, end_idx)`: pure index arithmetic (no cv2/ffmpeg), the same
`[shot_t - SHOT_PREROLL_S, hit_t or frame_times[-1]]` window spec 107
used to encode, now just telling the GUI which raw frame files to
read. `(-1, -1)` when nothing falls in the window (empty
`frame_times`, or the source raw frames genuinely gone by play time -
same "not available" case spec 107 already had to handle for its own
reasons, now simpler: no glob/frame-count mismatch to check for beyond
what `_shot_frames` does directly against the live directory).

`gui.py`: the whole thing is inline within the existing `"shots"` mode
- no new mode, no `ClipPlayer`. `_shot_frames(index)` resolves (and
caches) the selected shot's frame paths + times (shifted to start at
0) once per shot. `on_raw_shot_play_pause_click()` toggles play/pause
on the existing button (▶ / ❚❚, same pattern as the saved-clip
player's own toggle button); `_raw_playback_tick()` paints the current
frame via the existing `_show_frame()` and self-schedules the next one
via `root.after(ms, ...)`, where `ms` comes from the real gap between
this frame and the next (`frame_times[i+1] - frame_times[i]`) divided
by the selected speed - the same "trust the real per-frame timestamps"
principle spec 097 already established for the annotated video's
playhead, just driving a Tkinter timer instead of an ffmpeg concat
list. `_stop_raw_playback()` cancels any pending `after` and is called
whenever it must not keep painting over something else: switching
shots, or leaving shots mode entirely.

## Test plan

- `venv\Scripts\pytest tests\` - 105 passed, and noticeably faster
  (dropped 4 real-ffmpeg-encode tests for 5 pure-numpy ones on
  `select_shot_frame_range`: paired window, unpaired-to-end, preroll
  clamped at the recording's start, out-of-range and empty-input
  sentinels).
- `grep -rn build_shot_clip` across the repo returns nothing but this
  spec's and spec 107's own historical mentions.
