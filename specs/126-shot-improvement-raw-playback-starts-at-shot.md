# 126 — Raw playback and stepping start at the shot

Selecting a shot shows its image (the shot moment); "Toista raakakuvaa",
"+1 ruutu"/"-1 ruutu" and the slider used to start from the raw window's
first frame (2 s before the shot). `_shot_frames` now also returns the
frame index closest to the shot, and selecting a shot loads the window and
sets the position there, so Play and +1 continue from the shot.

Files: `gui.py`. Checked with a Tk smoke script (shot at 5.0 s -> "ruutu
61/121 2.00 s"; +1 -> 62; Play starts at 61). `pytest tests` 112 passed.
