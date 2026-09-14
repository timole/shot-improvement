# 088 — Shot-improvement: real 60fps capture at 1280x720

## What

Recorded clips (both raw and annotated) now capture at 1280x720 with a
real, measured ~58fps, up from 640x480 at a measured ~8fps. Two
independent bugs were found and fixed, not one:

1. `PREFERRED_VIDEO_LABEL = "logitech"` never matched this machine's
   camera - Windows/DirectShow reports it as `"c922 Pro Stream
   Webcam"`, which contains no `"logitech"` substring. Every prior
   recording silently fell back to `first_non_virtual_video_device_
   index()` and used a different, weaker `"USB Camera"` device instead
   - not the Logitech C922 the user actually records with in OBS.
   Fixed: `PREFERRED_VIDEO_LABEL = "c922"`.
2. Even once opening the right device, `cv2.VideoCapture`'s DirectShow
   backend defaults to uncompressed YUY2. At 1280x720@60fps that's
   ~110MB/s, far past USB2's real throughput, so the pixel format
   itself silently capped negotiated fps long before this machine's
   CPU was ever the bottleneck (contrary to spec 086's conclusion,
   reached before this was known). Fixed: request MJPG explicitly via
   `cap.set(cv2.CAP_PROP_FOURCC, ...)`, applied after width/height/fps.

## Why

The user records a comparable OBS setup (1280x720@60fps, same camera,
same machine) as a known-good baseline and asked for the same in this
app. Chasing that baseline is what exposed both bugs above - spec
086's CPU-bottleneck conclusion was investigated in good faith but was
wrong, on a machine that (per [[shot_improvement_camera_hardware_glitch]])
was also fighting an intermittent camera-driver glitch at the time,
which likely obscured both root causes.

## Out of scope and known constraints

- `SPECTROGRAM_HEIGHT` (still 480) is unchanged - it's independent of
  the video frame's own resolution, just the height of the panel
  stacked underneath it.
- No attempt to raise resolution/fps further than 1280x720@60 - this
  matches the user's own OBS baseline, not an arbitrary ceiling.
- Live idle preview (`LiveSession.read_frame`, non-recording branch)
  wasn't specifically re-measured at the new resolution/fps - it still
  runs pose inference every frame (a real per-frame cost, per spec
  086); the recording path (this spec's actual target) bypasses that
  entirely during capture, unchanged from spec 086.
- The MJPG-after-width/height/fps ordering is an empirically observed
  DirectShow/OpenCV quirk on this specific camera/driver, not a
  documented API guarantee - it may need re-verifying if the camera,
  its driver, or the OpenCV version changes.

## Acceptance criteria

1. `open_camera()` opens the Logitech C922 (not a fallback device) by
   matching on `"c922"`. `[T-088-01]`
2. `open_camera()` negotiates MJPG (not YUY2), verified by reading back
   `cap.get(cv2.CAP_PROP_FOURCC)` after opening, and the negotiated
   fps is close to the requested 60fps ceiling. `[T-088-02]`
3. A real recording's measured `actual_fps` (frame_count /
   elapsed_seconds) is close to 60, not the ~8fps seen before this
   spec. `[T-088-03]`
4. The encoded output clip is 1280x720 and its declared duration
   matches the requested recording duration (i.e. the measured-fps
   encode path from spec 084/086 still produces real-time playback
   speed at the new resolution). `[T-088-04]`
5. `add_spectrograms_to_frames()` no longer has a stale hardcoded width
   default that could silently mismatch the real frame width. `[T-088-05]`

## Test plan

- `[T-088-01]`, `[T-088-02]`, `[T-088-03]` live: ran `record_clip
  (duration_s=3.0, ...)` directly against real hardware. Logged
  `Resoluutio: 1280x720, 60.0 fps` (negotiated) and device name
  `c922 Pro Stream Webcam`; `Kuvia: 173 (57.7 fps toteutunut)`
  (measured). A standalone script additionally confirmed
  `cap.get(CAP_PROP_FOURCC)` reads back `MJPG` only when set after
  width/height/fps (before that ordering fix: stayed `YUY2`, real fps
  capped ~10).
- `[T-088-04]` live: inspected the encoded file with ffmpeg's own
  stream info: `Video: h264 ... 1280x720, ... 57.67 fps ... Duration:
  00:00:03.00` for a 3s recording.
- `[T-088-05]` code: `add_spectrograms_to_frames()`'s `width` parameter
  has no default now (was `640`); both call sites (`core/recorder.py`,
  `core/session.py`) pass `width=FRAME_WIDTH` explicitly.
- `pytest`: full suite (35 tests) passes unchanged.

## Implemented

- `core/recorder.py`:
  - `PREFERRED_VIDEO_LABEL`: `"logitech"` -> `"c922"`.
  - `FRAME_WIDTH`/`FRAME_HEIGHT`: `640x480` -> `1280x720`.
  - New `REQUESTED_FOURCC = "MJPG"`, applied in `open_camera()` via
    `cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*REQUESTED_
    FOURCC))` - after width/height/fps, not before (see "Out of
    scope" for why this ordering matters and isn't guaranteed stable).
  - `open_camera()`'s log line now also reports the negotiated fourcc,
    not just resolution/fps - the earlier bug would have been visible
    immediately if this had been logged from the start.
- `core/spectrogram.py`: `add_spectrograms_to_frames()`'s `width`
  parameter lost its `640` default (now required).
- `core/recorder.py` and `core/session.py`: both call sites of
  `add_spectrograms_to_frames()` now pass `width=FRAME_WIDTH`.

`[T-088-01]`-`[T-088-05]` verified this session (live hardware runs,
ffmpeg stream inspection, code reading, full test suite).
