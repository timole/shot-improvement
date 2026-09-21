# 129 — "Tallenna" button shows the recording length

The record button reads e.g. "Tallenna 30 s", following the duration field
(`gui.py`, `_update_record_button_text`; blank/invalid input shows plain
"Tallenna"; values under 1 show "1 s", matching the minimum actually used).

Checked while adding it: the 30 s recording `20260921094004` was captured
correctly - log `duration=30.0s`, 899 frames in 30.00 s (30.0 fps); the raw
mp4 is 640x360, 30.00 s, 29.97 fps (899 frames, 889 distinct) and the
annotated mp4 is 640x640, 30.00 s, 29.97 fps. No duration bug.
