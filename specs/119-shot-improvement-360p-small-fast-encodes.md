# 119 — 360p default, smaller and faster mp4 encodes

## What
- Capture is 640x360 (`FRAME_HEIGHT` 480 -> 360), requested directly from
  the camera (a supported 30fps MJPG mode) - no later resize step, so the
  raw frames are 360p from the first write; GUI `DISPLAY_SIZE` 640x360.
- ffmpeg encodes use `-preset veryfast -crf 32`, AAC 48k mono, and
  `-movflags +faststart` (`ENCODE_VIDEO_QUALITY_ARGS`,
  `ENCODE_AUDIO_QUALITY_ARGS` in `core/recorder.py`) - files go over a
  mobile network; quality matters less than size and speed.
- Annotated composite's spectrogram band 280 -> 160 px and claps band
  200 -> 120 px, so the annotated frame is 640x640 (was 640x840 for 360p).
  Fewer pixels to compose and encode.

## Measured
Re-annotating the same 10s clip: annotated mp4 2.95MB -> 0.99MB; whole
reprocess (extract + pose + compose + encode) 28s. A live end-to-end
recording timing was not obtained: the camera was in its stuck 1fps state
(reads of exactly 1.0s) after a hard kill of the previous process; needs
a USB replug.

## Tests
`venv\Scripts\pytest tests\` - 112 passed.
