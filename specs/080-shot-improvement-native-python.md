# 080 — Shot-improvement native Python app

**Status:** In progress

## What

Replaces the browser-based `/shot-improvement` page (specs 078/079,
now removed) with a native Python CLI: `experiments/shot-improvement/record.py`,
run directly on this laptop (`venv\Scripts\python record.py [seconds]`,
default 3s) — no browser, no FastAPI route, no server involved at all.
It opens the Logitech camera and Jabra mic directly (OpenCV +
sounddevice), runs MediaPipe's `PoseLandmarker` (same "lite" BlazePose
model as the browser version) locally per frame, draws a green box at
each visible hand's estimated palm center (wrist + pinky-knuckle +
index-knuckle average, same approach as spec 079), and saves both a
raw and an annotated clip (h264 video + aac audio, muxed with a
bundled ffmpeg binary) straight to
`experiments/shot-improvement/recordings/` on disk.

Lives in its own dedicated virtual environment
(`experiments/shot-improvement/venv/`), entirely separate from the
repo's root `venv/` — the FastAPI server process never imports
`opencv-python`/`mediapipe`, and this experiment's own
`requirements.txt` never touches `server/requirements.txt`.

## Why

The browser approach (specs 078/079) was abandoned because this
laptop has very little memory (~3.9GB total; ~324MB free was observed
with just a browser tab open) and a persistent Chrome tab running
MediaPipe's WASM build adds real, continuous memory pressure on top of
Chromium's own baseline footprint. A native Python process runs only
for the duration of one recording and then exits, releasing everything
back to the OS — no browser needed at all, and no memory held between
recordings. It also removes a layer of overhead the earlier approach
never needed for its own sake: getUserMedia/MediaRecorder/canvas
capture-stream muxing existed only to work around being inside a
browser tab in the first place.

A dedicated venv (rather than adding these packages to the server's
own `venv`) was chosen so the always-running FastAPI server process
(which now has no reason to touch this experiment at all) never pays
any memory cost for opencv/mediapipe being importable, even
hypothetically.

## Out of scope and known constraints

- Camera/mic selection is name-based, same idea as the browser version
  (prefer a device whose name contains "Logitech"/"Jabra", else fall
  back): but this laptop's DirectShow camera name was observed as the
  generic "USB Camera", not literally "Logitech" - the fallback (first
  non-"virtual" video device) is what actually selects it in practice
  right now. Audio similarly falls back to the system default input
  when no device name contains "jabra" (observed: the Jabra wasn't
  connected during development, only the laptop's Realtek mic was).
- Real per-frame throughput with pose inference running was measured
  at ~7-8fps on this hardware, well under the camera's nominal 30fps.
  The final clip's frame rate is set to the *actually achieved* fps
  (frame_count / real_elapsed_seconds), not the camera's nominal rate
  — see "Implemented" for why a naive approach silently produced a
  sped-up, wrong-duration clip.
- No live preview window - this is a blind, fixed-duration CLI
  recording (prints device names, frame count/fps, and the two output
  paths). A live OpenCV preview window is a reasonable future addition
  but wasn't asked for.
- Frames are written to a temporary PNG sequence during capture, then
  encoded once via ffmpeg at the end - never buffered as an in-memory
  list, to stay lightweight on this machine's limited RAM.
- Only the palm position, same as spec 079 (no racket/stick detection
  yet) - this file replaces the *runtime*, not the detection scope.
- No automated test can exercise the real camera/mic/model end-to-end
  (no hardware in CI) - the pure logic (palm-center math, device-name
  matching, filename formatting) is unit-tested; the full pipeline was
  verified with a real, manual recording during development instead
  (see "Implemented").

## Acceptance criteria

1. `python record.py` (from `experiments/shot-improvement/`, its own
   venv activated) records for a given duration (default 3s) using the
   Logitech camera/Jabra mic when present, otherwise a sensible
   fallback device, without erroring. `[T-080-01]`
2. The saved raw clip's real playable duration matches the requested
   duration (not sped up/slowed down by a wrong declared frame rate).
   `[T-080-02]`
3. Both a raw and a palm-box-annotated clip are saved into
   `experiments/shot-improvement/recordings/`, each with both a video
   and an audio stream. `[T-080-03]`
4. Palm-center math (wrist + pinky-knuckle + index-knuckle average,
   visibility-gated) and device-name matching are covered by offline
   unit tests, runnable via this experiment's own dedicated venv.
   `[T-080-04]`
5. The old browser page, its FastAPI route/mount, and its
   `POST /api/shot-improvement/save-recording` endpoint are removed
   entirely - nothing under `/shot-improvement` is served by
   `server/main.py` any more. `[T-080-05]`

## Test plan

- `[T-080-04]` offline: `experiments/shot-improvement/venv/Scripts/pytest experiments/shot-improvement/tests/`
  (deliberately NOT in the root `pytest.ini`'s `testpaths` - the root
  venv has no opencv/mediapipe by design, see "Why").
- `[T-080-05]` offline: `python -m pytest server/tests/` (root venv) -
  no `/shot-improvement` route exists any more.
- `[T-080-01]`–`[T-080-03]` live/manual: run
  `python record.py 3` with the real hardware, inspect the two output
  files' `ffmpeg -i` stream info for a ~3.00s duration with one video
  and one audio stream each.

## Implemented

Shipped as `experiments/shot-improvement/core/{devices,pose,recorder}.py`
+ `record.py`, with its own `requirements.txt` and dedicated
`venv/` (not committed). `server/main.py`'s `/shot-improvement` mount
and `POST /api/shot-improvement/save-recording` route (and their
tests) were deleted.

All criteria verified with a real recording on this laptop during
development: `python record.py 3` opened "USB Camera" (the DirectShow
name actually reported; matched via the "not virtual" fallback since
it isn't literally named "Logitech") and the system default mic (no
"Jabra" device was connected at the time), ran `PoseLandmarker` per
frame, and produced two files, each verified via `ffmpeg -i` to have
`Duration: 00:00:03.00` with h264 video + aac audio streams.

A real bug was caught and fixed during that verification: the first
implementation declared the camera's nominal fps (30) to
`cv2.VideoWriter` up front, then tried to correct the mismatch between
that and the actually-achieved ~7fps by passing `-r <actual_fps>` to
ffmpeg as an *input* option when muxing - this silently produced a
0.77s clip from a 3-second recording, because `-r` as an input flag is
ignored for an already-timestamped container format (confirmed via
`ffmpeg -i` on the broken output). Fixed by writing frames as a PNG
sequence instead (no embedded timing to fight) and encoding once at
the end with `-framerate <frame_count/real_elapsed_seconds>`, which is
exact by construction.

`[T-080-04]` verified: 14 unit tests pass in the dedicated venv.
`[T-080-05]` verified: 202/202 tests pass in `server/tests/` (down
from 206, the 4 removed `shot-improvement` route tests) with no
`/shot-improvement` route left. `[T-080-01]`–`[T-080-03]` verified via
the real recording described above; not yet re-verified with a human
actually in frame (the one test recording made during development
pointed at an empty/dark scene, so no palm was actually detected in
it - the palm-box logic itself is separately covered by
`[T-080-04]`'s unit tests).
