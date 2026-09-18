# 107 — Shot-improvement: play the raw video behind a shot

## What

Each shot in spec 106's browsable list now has a "▶ Toista raakakuvaa"
button next to its speed. Clicking it plays the RAW (unannotated)
footage from 2 seconds before the shot's recognized instant through to
the recognized hit (or, for a shot with no paired hit, through to the
end of the recording), using the app's existing playback transport
(play/pause/step/skip/speed/volume/loop). Finishing the clip, or
clicking back, returns to the shot list rather than the live camera
feed.

## Why

Spec 106 shows a still image per shot; this closes the gap between
"here's a number" and "here's what actually happened" by letting the
user watch the moment itself.

## Design

The trimmed clip is built **lazily, on first Play click**, not
eagerly alongside the shot list - keeps "Vain nauhoitus" exactly as
fast as spec 106 left it (nothing extra runs until a shot is actually
played). A build is cached per shot for the rest of the session, so a
second click on the same shot plays instantly.

`core.compose.build_shot_clip()` (new): copies the raw frame files
whose real timestamps fall in `[shot_t - SHOT_CLIP_PREROLL_S (2.0s),
hit_t or the recording's own end]` into a fresh temp dir, renamed
sequentially (`core.recorder.encode_frames_with_audio`'s concat-list
builder assumes exactly that naming from index 0, not the source's
real filenames - see `_write_concat_list`), slices the matching audio
out of the recording's own `audio.wav`, and reuses
`encode_frames_with_audio` to produce a normal small mp4. Returns
`False` (no exception) rather than raising when the source raw frames
are already gone - the accepted tradeoff of building lazily: a normal
recording's temp dir may already be cleaned up, or a "Vain nauhoitus"
item may already have been processed via "Käsittele odottavat" (its
`pending/<ts>/raw/` deleted at that point). The GUI shows a clear
"Raakadataa ei ole enää saatavilla tälle laukaukselle." status instead
of crashing.

`ShotImage` gained `hit_t: Optional[float]` (needed as the playback
window's end boundary - `speed_kmh` alone can't be reversed back into
it). `core.session.LiveSession` gained a `ShotSource` dataclass
(raw_dir, audio_path, frame_times, actual_fps) dispatched alongside
`on_shots_ready`'s shot list in both the normal and deferred paths, so
the GUI always knows where a recording's raw assets live without
guessing per mode.

`gui.py`: a speed label + Play button row above the shot list;
clicking Play runs `build_shot_clip` on a background thread (mirrors
`on_remove_background_click`'s pattern) and, on success, reuses
`load_and_play()` as-is for the actual playback - no new player code.
A `_shots_playback_active` flag (set only by this path, reset by
`on_play_selected` for a normal saved recording) tells
`on_back_to_live_click` whether to return to `_restore_shots_mode()`
(re-pack the shot list, re-show the previously-selected shot) instead
of going live; the back button's own label switches between "Takaisin
laukauksiin" and "Takaisin livekuvaan" to match. Cached clip temp dirs
are best-effort cleaned up in `on_close()`.

## Test plan

- `venv\Scripts\pytest tests\` - 104 passed. New `build_shot_clip`
  tests in `tests/test_compose.py`: real-encode duration roughly
  matches `[shot_t - 2, hit_t]` and, for an unpaired shot,
  `[shot_t - 2, frame_times[-1]]`; returns `False` for a missing
  `raw_dir` and for a frame-count/frame_times mismatch.
- Real GUI recording, normal mode: shot list appears, Play on shot 1
  starts ~2s before the shot sound with the raw (no boxes/spectrogram)
  footage, ends around the hit; back button returns to the same shot
  still shown, relabeled "Takaisin laukauksiin" while playing.
- Same clip, "Vain nauhoitus": shot list still appears immediately (no
  extra delay), Play still works, built from `pending/<ts>/raw`.
- Second Play click on the same shot plays instantly (cached, log
  shows only one `build_shot_clip: built` line for it).
