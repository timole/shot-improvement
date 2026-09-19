# 115 — Shot-improvement: capture at 640x360, 30 fps

## What

`core/recorder.py` now requests 640x360 at 30 fps (`FRAME_WIDTH`,
`FRAME_HEIGHT`, `REQUESTED_FPS_CEILING`), instead of 1280x720 at 60 fps.
The palm box in `core/pose.py` is halved to match (`BOX_SIZE` 40 -> 20,
line thickness 4 -> 2) so it keeps the same relative size on the frame.

## Why

Spec 113 measured ~30 fps as this camera's real ceiling at any
resolution, so the 60 fps request bought nothing. 640x360 MJPG at 30 fps
is a supported camera mode, and a quarter-size frame is much lighter on
this 3.83GB machine's memory and disk. It also matches the GUI's
`DISPLAY_SIZE` (640x360).

## Files

- `core/recorder.py` - resolution and fps constants.
- `core/pose.py` - `BOX_SIZE`, box line thickness.

## Test plan

- `venv\Scripts\pytest tests\` - 112 passed.
- Real recording to confirm the negotiated mode is 640x360 @ ~30 fps.
- The composed spectrogram and claps bands keep fixed pixel heights, so
  they are proportionally larger against a 360-tall frame. Not changed.
