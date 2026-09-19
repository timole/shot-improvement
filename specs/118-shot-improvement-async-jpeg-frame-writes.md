# 118 — Async JPEG raw-frame writes; resolution/fps sweep

## What
- Raw frames are written by `core.session.FrameWriter` (2 background
  threads) as JPEG q95 (`FRAME_FILE_EXTENSION = "jpg"`), not inline BMP.
- Capture stays 640x480, requested 30 fps.

## Measurements (this machine, C922, MJPG, manual exposure)
- Bare `cap.read()` sweep (4s each, 60fps requested): 160x90 ... 1080p
  all deliver ~30 unique fps except **1280x720 = ~48-60 fps**. Smaller
  frames do not give more fps.
- Inline disk writes at 1280x720 stalled 0.4-2s at a time (BMP 2.7MB:
  9-18 fps; JPEG inline: 45 fps). With the async JPEG writer the queue
  stays empty and capture is no longer disk-bound: 640x480 recording
  loop measured 31 fps, max gap 50ms, no stalls (was 15 fps, gaps to 1s).
- **Any active audio input stream (any mic, any process, any
  samplerate/blocksize) halves camera delivery from 60 to 30 fps** at
  1280x720; stopping the stream restores 57-60. Not fixed by timer
  resolution or process priority. Shot detection needs audio, so 30 fps
  is the working ceiling while recording; hence 640x480 (same as OBS
  defaults) is the default.
- Rapid open/close cycling wedges the camera (1 fps / won't open) until
  a USB replug - unchanged.

## Notes
- Old pending items holding `.bmp` frames would not be found by the
  `.jpg` glob; none were pending at the time.
