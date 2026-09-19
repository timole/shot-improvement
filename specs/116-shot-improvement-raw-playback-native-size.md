# 116 — Raw shot playback at native frame size

## What
"Toista raakakuvaa" now shows each raw frame at its own size (640x360)
instead of the shot image's display size (640 wide x the taller
frame+spectrogram height), which had stretched the raw video vertically.

## Files
- `gui.py` - `_raw_playback_tick` sets `_display_size` from the frame.

## Test plan
- `venv\Scripts\pytest tests\` - 112 passed. Playback itself checked by hand.
