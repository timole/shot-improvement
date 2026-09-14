# 086 — Shot-improvement performance: GPU check, deferred pose inference

**Status:** In progress

## What

Investigated whether this laptop's weak, ~4GB-RAM hardware could
capture recordings fast enough to actually see a stick bend (the
user's concrete complaint: real captured fps was low enough that the
bend happens *between* captured frames). Two concrete changes:

1. **Confirmed GPU acceleration is not available at all**, rather than
   guessing - tried creating a `PoseLandmarker` with
   `BaseOptions.Delegate.GPU` directly; it fails immediately
   (`GPU processing is disabled in build flags`). The pip-installed
   `mediapipe` package on Windows simply doesn't ship a working GPU
   delegate. Nothing to enable, no code path to add.
2. **Deferred pose inference out of the live capture loop.** Pose
   inference (~57ms/frame, measured in spec 083/084's era) used to run
   on every frame *during* capture, competing directly with the
   camera read + disk write for the same time budget. It's now a
   separate pass (`core.pose.annotate_frames_dir()`) that runs once,
   after capture ends, over the already-saved raw frames - capture
   itself now only does a camera read + one disk write per frame.

## Why

Framerate can't be raised by asking the camera for more (it already
negotiates up to 60fps; the bottleneck was always this CPU, not the
camera - established in the FPS investigation this session's summary
already covers). GPU acceleration was the next lever to check before
concluding nothing more could be done - checked directly rather than
assumed unavailable or assumed working. With that ruled out, the
remaining lever was removing CPU work from the hot capture loop
itself, not making that work faster.

## Out of scope

- No change to the *idle* live-preview loop (outside of an active
  recording) - it still runs pose detection on every frame, same as
  before. The user's complaint was specifically about recorded clips
  losing fast motion between frames, not about idle preview
  smoothness, and idle preview isn't time-boxed the way a recording
  is.
- No attempt at true multi-threaded/multi-process capture (e.g. a
  separate process purely for camera reads) - a much bigger
  architectural change, not attempted here.
- Does not fix, and cannot fix from software: **this specific laptop's
  camera was found, during this same investigation, to currently be
  delivering only ~1 frame/second of near-black frames** - see
  "Implemented" below. This is a real, currently-present hardware/
  driver-level problem on this machine, confirmed with zero
  app-specific code involved, and is unrelated to (and would defeat)
  any of the software changes above. Worth checking physically
  (camera cable/USB port, any privacy shutter, room lighting) -
  outside what this session can fix.

## Acceptance criteria

1. `BaseOptions.Delegate.GPU` is verified (not assumed) to fail on
   this machine's MediaPipe install, and that finding is documented
   rather than silently left unexplored. `[T-086-01]`
2. During recording, the capture loop no longer calls
   `PoseDetector.detect()` - only a camera read and one `imwrite` per
   frame. `[T-086-02]`
3. The final annotated clip is pixel-equivalent in *content* to the
   old approach (same palm-box detection, just computed afterward) -
   unit tests for `draw_palm_boxes`/`palm_boxes_from_landmarks` still
   pass unchanged, since that logic itself didn't move. `[T-086-03]`
4. Real captured fps during a recording measurably improves *when the
   camera itself is delivering frames normally* - not verified live
   this session due to the hardware issue above; the mechanism (one
   disk write instead of pose-inference + two disk writes per frame)
   is the same class of change spec 084's PNG->BMP switch made, which
   *was* measured (6.6fps -> 9.3fps). `[T-086-04]`

## Test plan

- `[T-086-01]` live: a standalone script requesting the GPU delegate
  directly, observing the exact failure message.
- `[T-086-02]`, `[T-086-03]` unit tests (`tests/test_pose.py`, already
  existing - unaffected since the pure functions didn't change) plus
  reading through `core/recorder.py::record_clip` and
  `core/session.py::LiveSession.read_frame`/`_start_finish_recording`
  to confirm `detect()` no longer appears in either capture loop.
- `[T-086-04]` **not verified live this session.** Attempted via both
  the CLI recorder and a raw, app-code-free `cv2.VideoCapture.read()`
  loop; both showed the camera itself returning a new frame only once
  per ~1000ms (measured as *exactly* 1000.0ms repeatedly, not "slow
  and variable" - consistent with a driver-level fallback/placeholder
  frame source, not real capture), with near-zero mean pixel
  brightness. This matches, and is presumably the same root cause as,
  intermittent `read_frame: took 1.0x s` warnings logged earlier in
  this same session's live GUI testing. Reproducible across two
  separate fresh camera opens in a row - not a one-off glitch. This
  makes any live fps measurement meaningless right now (the app would
  correctly report ~1fps, but that's the camera, not the app). Needs
  re-verification the next time the camera is confirmed to be
  delivering a normal live image (as it clearly was earlier this same
  session - see spec 085's screenshots).

## Implemented

- `core/pose.py`: new `annotate_frames_dir(raw_dir, annotated_dir,
  extension, fps)` - opens a fresh `PoseDetector`, walks the raw frame
  files in order, reconstructs a per-frame timestamp from the index
  and the clip's real measured fps (consistent with how encoding
  already derives its own framerate), and writes annotated copies.
  Pure I/O + the same `palm_boxes_from_landmarks`/`draw_palm_boxes`
  used before - no detection logic changed, only when it runs.
- `core/recorder.py::record_clip`: capture loop no longer opens a
  `PoseDetector` or calls `detect()`/`draw_palm_boxes` at all; writes
  only the raw frame. `annotate_frames_dir()` is called once, after
  the loop, before the spectrogram is composited in (must run first -
  spectrogram compositing changes the annotated frame's size).
- `core/session.py::LiveSession.read_frame`: while `self._recording`,
  skips `self.detector.detect()`/drawing entirely, writes only the raw
  frame, and returns the *undecorated* frame for the live preview to
  display for the duration of the recording (palm boxes reappear once
  the clip is loaded back for playback, on the annotated file, where
  `annotate_frames_dir()` has since run). Idle (non-recording) preview
  is unchanged. `_start_finish_recording`'s background worker calls
  `annotate_frames_dir()` before `add_spectrograms_to_frames()`, same
  ordering reason as the CLI path.
- GPU delegate: tried and rejected, not left unexplored - see
  "Acceptance criteria" #1. No code path added since there is nothing
  to call.

`[T-086-01]`-`[T-086-03]` verified this session (direct script output;
unit tests; code reading). `[T-086-04]` blocked by an unrelated,
currently-present camera hardware issue - see "Out of scope" and
"Test plan".
