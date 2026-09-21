# 128 — Shot position picker (blue line by default)

The puck speed is `distance / (hit time - shot time)`. Until now the
distance was fixed at 57 m (own goal line to the far end boards on a 61 m
rink). The GUI now has "Ammuntapaikka:" with five choices (`core.claps.
SHOT_POSITIONS`), default the blue line:

| Choice | Distance to the goal |
|---|---|
| Sinisestä viivasta maaliin (default) | 18.5 m |
| Keskiviivalta maaliin | 26 m |
| Toisesta sinisestä viivasta maaliin | 33.5 m |
| Oman alueen aloituspisteiden linjalta maaliin | 46 m |
| Päätyviivalta vastapäiseen päätyyn (the old one) | 57 m |

IIHF rink used for the new ones (60 m long; goal line 4 m from the end
boards; blue lines 15 m apart = 22.5 m from the end boards; end-zone
face-off spots 6 m from the goal line): shooter's end boards = 0, far goal
line = 56 m, so 56 - 37.5 / 30 / 22.5 / 10 (the "other" blue line is the one
in the shooter's own zone; the dots line is the own-zone face-off spots).
Sources: IIHF rink spec as summarised on Wikipedia ("Ice hockey rink":
goal line 4.0 m from end boards, blue lines 15.0 m apart) and the IIHF
rulebook's end-zone face-off spots 6 m from the goal line.

Also scaled with the distance (`hit_delay_window`): the shot-to-hit delay
window used to pair a shot with its hit (1.5-4.0 s for 57 m -> 0.49-1.30 s
for 18.5 m), and the minimum spacing between detected events (never larger
than 0.8x the shortest plausible delay). The 57 m behaviour is unchanged.

The chosen distance is used for the annotated video, the shot images and the
speed labels, and is stored in `pending/<ts>/meta.json` (`distance_m`) so a
"Vain nauhoitus" recording is processed later with the position it was
recorded with (older pending items default to 57 m).

Files: `core/claps.py`, `core/compose.py`, `core/pending.py`,
`core/session.py`, `gui.py`, `tests/test_claps.py`. `pytest tests`: 115 passed.
