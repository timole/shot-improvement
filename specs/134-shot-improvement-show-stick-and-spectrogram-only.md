# 134 — "Show stick" button; annotated video with the spectrogram only

## Annotated video: no more black timestamp band
The black band under the spectrogram (white shot timestamps) is removed:
`render_claps_band`/`CLAPS_BAND_HEIGHT` are gone, the annotated composite is
frame + spectrogram (640x360 + 160 = 640x520). The spectrogram still carries
the shot-to-hit lines and km/h. `shot-improvement-20260921103414-annotated.mp4`
was regenerated (it uses the 6 m distance it was recorded with).

## "Show stick" (below the video)
A toggle that draws the stick, in light green, between the player's two
hands on the frame being viewed (saved-clip playback, stepping, scrubbing,
and the raw-frame shot view): a straight line if the stick is straight, an arc
if it bends. The status text beside it says "Maila on suora" / "Maila taipuu
~N px" / "Molempia käsiä ei tunnistettu".

How (`core/stick.py`):
- The ends are the two palm centres - the middle of the yellow squares. For
  annotated clips those are read from `recordings/shot-improvement-<ts>-hands.json`,
  written by the annotation pass (`compose_annotated_frames(hands_json=...)`,
  the exact box centres, looked up by time); clips without that file, and the
  raw shot frames, use a pose detector in IMAGE mode (hand-visibility bar 0.2
  instead of 0.5: a hand on a stick is often only 0.3-0.4 visible).
- The stick is searched in the RAW frame (the annotated clip's yellow boxes
  cover the hands): of the family of arcs from one hand to the other
  (parabola with sideways bulge up to 25% of the hand distance) the one with
  the strongest thin-line contrast along it wins; a straight line is preferred
  unless an arc is clearly better (1.25x contrast, >= 1.5 px bulge).
- Colour is not the cue: on this camera at the fixed short exposure the light
  green shaft measured pale and grey (HSV ~ (110, 60-76, 100-120)) and shows
  as a dark line against bright sky, so "brighter/darker than both flanks" is
  used instead. The line is drawn light green with a thin dark outline.

Limits: one parabola per stick (no S-bends); the endpoints are only as good
as the pose's hand positions; where the stick is hidden behind the body the
result is a straight chord; playback with the button on is slower (one pose
detection per frame).

Files: `core/stick.py` (new), `core/compose.py`, `core/session.py`,
`core/pending.py`, `core/pose.py` (`min_visibility`), `gui.py`,
`tests/test_stick.py`, `tests/test_compose.py`, `tools/bench_record.py`.
`pytest tests`: 124 passed.
