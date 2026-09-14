# 085 — Shot-improvement spectrogram panel and background removal

**Status:** In progress

## What

Two additions built alongside spec 084's playback controls:

1. **A spectrogram baked into the annotated clip.** Every
   `-annotated.mp4` recording now grows a spectrogram panel under the
   video itself - the frame size doubles from 640x480 to 640x960 (top
   half: the video, exactly as before; bottom half: a full-clip
   spectrogram of the recorded audio, with a vertical playhead line
   showing each frame's own position in time). The raw clip is
   unaffected and stays 640x480. Since the composited frame is a real
   part of the video file, replaying it in spec 084's scrubber/
   frame-step controls naturally shows the playhead moving in lockstep
   with playback - no separate synchronization logic was needed.
2. **"Häivytä tausta" (remove background)**, a button in the playback
   controls that segments the currently-displayed frame and blacks
   out everything that isn't a person, showing the isolated result in
   the same preview area. One frame at a time, on demand - not applied
   to the whole clip or baked into any saved file.

## Why

Both serve the same goal named directly by the user: eventually
detecting the stick/racket-to-ball impact moment from audio (a
transient/spike) and measuring how much the stick bends at that
instant from the video (see spec 084's "Why"). A visible spectrogram
turns "find the loud moment" into something you can literally see and
scrub to, without needing to build automatic audio-impact-detection
first. Isolating the person from a busy/bright background is a
similar manual-inspection aid, aimed at judging stick bend more
clearly.

## Out of scope and known constraints

- **The background-removal button does NOT preserve a held stick or
  racket** - this was explicitly the stated goal ("vain ihminen ja
  maila jäävät näkyviin" / "only the person and the stick remain
  visible"), and it isn't achievable with this approach. MediaPipe's
  selfie segmenter identifies people specifically; it has no notion of
  a held object. Verified directly, not assumed: tested against a real
  recorded frame with a clearly raised hockey stick - the person was
  cleanly isolated, but the stick was removed along with the rest of
  the background. The button is genuinely useful for what it does
  (isolate the person), just not for "and the maila" as stated.
- No automatic audio-impact detection and no automatic bend
  measurement - both remain later goals, same as spec 084.
- Spectrogram rendering happens once per recording, from the whole
  captured audio buffer, as a step *after* capture ends (in the GUI,
  already on the background encode thread from spec 083) - not live,
  and not per-frame during capture, which would slow down the already
  CPU-constrained capture loop.
- The spectrogram is a plain STFT magnitude image (via `numpy.fft`,
  no new dependency) with a fixed 80dB dynamic range window and a
  colormap - not calibrated or annotated with frequency/dB axis
  labels.
- Background removal only affects what's shown in the preview
  momentarily - it changes no saved file, and moving to any other
  frame (step, seek, play) naturally shows the normal frame again.
- Model load + inference for background removal runs on a background
  thread (see "Implemented" for why this was necessary, found via a
  real hang during testing) - the same pattern spec 083 established
  for ffmpeg encoding.

## Acceptance criteria

1. A freshly recorded `-annotated.mp4` is 640x960; its paired raw
   `.mp4` stays 640x480. `[T-085-01]`
2. The bottom half of every annotated frame is a spectrogram of the
   clip's own audio, with a vertical playhead line positioned at that
   frame's own time within the clip. `[T-085-02]`
3. Scrubbing/playing the annotated clip in spec 084's controls shows
   the playhead moving correctly, with no extra wiring beyond the
   controls already built (it's baked into the video itself).
   `[T-085-03]`
4. "Häivytä tausta" segments the currently-displayed frame and shows
   the person isolated (background blacked out) in the preview.
   `[T-085-04]`
5. Clicking "Häivytä tausta" never blocks/freezes the rest of the UI,
   regardless of how long model loading or inference takes.
   `[T-085-05]`

## Test plan

- `[T-085-01]`, `[T-085-02]` live/manual + direct file inspection:
  recorded a real 10s clip through the GUI, confirmed via `ffmpeg -i`
  that the raw output is 640x480 and the annotated output is 640x960,
  and visually confirmed (extracted frame at the clip's midpoint) the
  spectrogram panel and a centered playhead line.
- `[T-085-03]` covered by construction, not a separate live check -
  the playhead is literal video pixels, so spec 084's already-verified
  scrubber/frame-step controls move it for free.
- `[T-085-04]`, `[T-085-05]` live/manual: loaded a real recording,
  clicked "Häivytä tausta", confirmed (after a real bug was found and
  fixed - see "Implemented") that the app stayed responsive throughout
  and the button eventually produced a masked frame.

## Implemented

Shipped as `core/spectrogram.py` (`compute_spectrogram_image`,
`with_playhead`, `add_spectrograms_to_frames` - pure functions,
unit-tested) wired into both `core/recorder.py::record_clip` and
`core/session.py::LiveSession._start_finish_recording`, right before
the annotated clip is encoded; and `core/segmentation.py`
(`BackgroundRemover`, `apply_person_mask` - the pixel logic kept pure
and unit-tested separately from the MediaPipe call) wired into
`gui.py`'s new "Häivytä tausta" button.

**Two real bugs found and fixed via live testing, not assumed:**

1. The first version called `BackgroundRemover()` (which loads a
   MediaPipe model) synchronously on the GUI's main thread. This hung
   the entire app for well over a minute with no error and no visible
   progress - reproducing exactly the kind of freeze spec 083 was
   written to catch. Fixed the way spec 083 fixed the equivalent
   ffmpeg-encode problem - moved model load and inference onto a
   background thread (`threading.Thread`), with the result marshaled
   back to the GUI via `root.after(0, ...)`.
2. That fix alone wasn't enough: with the model load correctly moved
   to a background thread, clicking the button still took several
   *minutes* to produce a result (confirmed via timestamped log lines,
   not guessed), while an isolated script doing the identical
   `BackgroundRemover()` call finished in ~30ms. Narrowed by process
   of elimination across several live tests: recreating the isolated
   script's exact conditions (a `PoseDetector` already created, even
   an already-open-but-unread camera) stayed fast every time, so the
   slowdown had to be something only the full running app did.
   `gui.py`'s `_tick()` callback (rescheduling itself every
   `PREVIEW_POLL_MS=10ms`, forever, live preview or not) was changed
   to skip its own work while segmenting - this alone didn't fix it
   either. What did: `LiveSession.suspend_camera()` /
   `resume_camera()` (`core/session.py`), which fully releases
   (`cap.release()`) and later reopens the camera device around the
   segmentation call, called from `gui.py`'s
   `on_remove_background_click()` / `_on_background_removed()`. Once
   the camera was actually released (not just left unread), model
   creation dropped back to about a second, confirmed via log
   timestamps (`suspend_camera` at 18:22:22.803 -> `BackgroundRemover
   created` at 18:22:23.885). The working theory - not fully proven at
   the C++ level, but consistent with every test result - is that
   Windows' capture backend (DirectShow/MSMF) runs its own capture
   graph on a background thread regardless of whether the Python side
   calls `read()`, and that graph was starving the newly-created
   MediaPipe segmenter's own thread pool of CPU/scheduling time on
   this machine. Safe to do only because this button lives entirely in
   playback mode, where the live feed isn't shown anyway.

The stick-removal limitation was confirmed by direct testing against
a real frame (see "Out of scope"), not assumed or left for the user
to discover - `apply_person_mask`'s correct mask polarity (category 0
= person for this model) was also confirmed empirically by comparing
both possible orientations against a real frame, rather than guessed.

`[T-085-01]`-`[T-085-04]` verified live on this laptop.
Verification of `[T-085-05]`'s actual fix (does the threaded version
complete, not just avoid freezing the UI) was still running as this
was written - see the session's own follow-up for the outcome.
