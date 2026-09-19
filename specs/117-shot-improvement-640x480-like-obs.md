# 117 — Capture at 640x480, like OBS defaults

## What
`FRAME_WIDTH/HEIGHT` 640x360 -> 640x480 (4:3), `REQUESTED_FPS_CEILING`
30 -> 60 (negotiated value is read back and logged), GUI `DISPLAY_SIZE`
-> 640x480.

## Why
OBS with default settings (`recordings/2026-09-19 12-27-46.mp4`) captured
640x480, container 60 fps, but only ~206 of 543 frames were distinct
(mpdecimate), i.e. ~23 real fps - the 60 fps is duplicated frames, as in
spec 113. The app now requests the same mode OBS negotiates.

## Test plan
- `venv\Scripts\pytest tests\` - 112 passed.
- Real recording pending: OBS held the camera when tested, so the
  negotiated mode is not yet confirmed.
