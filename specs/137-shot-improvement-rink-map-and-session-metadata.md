# 137 — Rink map for "Ammuntapaikka", the target the puck hits, and per-session metadata

## Shooting place and target

The "Ammuntapaikka" combobox is replaced by a labelled box "Ammuntapaikka ja
kohde" at the bottom of the centre column under the video (packed
`side="bottom"`, so the timeline keeps sitting right under the video and the
tall left column does not grow). It holds a distance field ("Matka: 18,5 m",
2-60 m, comma or dot), a "Kohde" choice - "Maali" or "Päätylaita" - and an
IIHF rink map. The speed is computed over the distance from where the shooter
stands to what the puck hits, and the sound (or picture) of that hit is what
ends the measured flight: the goal (its goal line, 56 m from the shooter's end
boards - what every distance in `core.claps` was measured to) or the end
boards (60 m). The field is the one source of truth; the map is only a
shortcut for typing it, and `distance = target x - shooter x`.

The map shows the rink with the shooter's end on the left and the target end
on the right. The six named places that are a line across the rink (own goal
line, own face-off spots line, own blue line, centre line, attack blue line,
attack face-off spots line) are dashed green lines with a clickable green chip
on top, and the chip shows the place's distance to the current target:

| place | to the goal | to the end boards |
|---|---|---|
| own goal line | 52 | 57 |
| own face-off spots line | 46 | 50 |
| own blue line | 33,5 | 37,5 |
| centre line | 26 | 30 |
| attack blue line | 18,5 (default) | 22,5 |
| attack face-off spots line | 6 | 10 |

The 57 for the own goal line to the end boards is the original measurement
(specs 098-127, a 61 m rink) kept as asked; this 60 m rink's geometry gives 56.
A click on a chip or near a line (within 1,5 m) sets that place, a click
anywhere else on the ice sets the metres to the target rounded to 0,1 m;
clicks that leave less than 2 m to the target are ignored. Clicking the goal
or the end boards on the map picks that target (the same as the radio
buttons); the chosen target is highlighted in orange, and the chosen distance
is marked with an orange dot and an arrow to it.

Changing the target keeps the shooter where they stand: a named place moves to
its own distance for the new target, any other distance moves by the gap
between the two targets (4 m: goal to end boards), clamped to 2-60 m. The
label under the field names place and target ("Sinisestä viivasta maaliin
(18,5 m)", "Sinisestä viivasta päätyyn (22,5 m)", or "11 m maaliin" for any
other distance - `core.rink.describe`; the goal wordings are the old presets'
own). The old "camera on the goal: blue line to the end" preset is now just
the blue line with the end boards as target (22,5 m); the 19,62 m camera-
behind-the-goal preset is no longer named (typing it says "19,62 m maaliin").
An invalid entry says "Anna matka metreinä (2-60 m)." and "Tallenna" refuses
to start ("Anna ammuntamatka metreinä (2-60 m)."); distance and target are
read when the button is clicked, not when the countdown ends. "Palauta
oletukset" puts them back to 18,5 m and "Maali".

## Session metadata

`save_shot_images` (every path that makes shot images - live, deferred and
pending processing - goes through it) now also writes
`shot-improvement-<ts>-session.json` next to the images: `distance_m`,
`target` ("goal" / "end"), `duration_s`, `fastest_kmh` and each shot's `index`
and `speed_kmh` (null for a shot with no audible hit). The target travels
with the distance: `LiveSession.start_recording(shot_target=...)`, and for
"Vain nauhoitus" the pending entry's `meta.json`, so a clip processed later
still says what it hit (an entry without one is the goal). The file is
written even when no shot was found, so a session still says where it was shot
from. A failure to write it is logged and does not fail the recording.

## Recordings list

The list is one row per session instead of one per file (raw and annotated
clip and the shot images of one timestamp belong together): columns "Luotu",
"Ammuntapaikka" (place and target, or "N m maaliin") and "Nopein". The shot
images are no longer separate rows. Selecting a row shows a "Tallenteen tiedot"
box with the place, duration, fastest shot and shot count, and a list of its
shots ("Laukaus 2: 112 km/h (…-shot-02.jpg)"); picking one shows that image
in the preview area (the spec 125 still-image mode, "Takaisin livekuvaan"
leaves it). Double-click / "Toista" plays the annotated clip, or the raw one if
there is no annotated clip yet. "Poista" deletes the whole session (asks
first, naming the file count): both mp4s, the shot images and the sidecars,
with the same cloud tombstones for the two mp4s as before. Selection survives
the list refreshes a finishing recording triggers.

Recordings made before this have no metadata: they list with place "—" and
no fastest shot, and their shot images are still shown in the details box
(without speeds). Their speeds are not recomputed. A session whose metadata
has no target (written before the target existed) is named by the old presets
and counted as a goal shot.

## Fix: the shot image strip used 57 m

`save_shot_images` drew the yellow km/h on each shot image's spectrogram strip
with `draw_pair_annotations`' 57 m default instead of the recording's
distance, so a 46 m session read 128 and 125 km/h in the shot list but 159
and 154 km/h in the strip (128,5 x 57/46 = 159). The list, the number burned
into the frame and the annotated video already used the right distance; the
strip now does too (a test checks the distance passed on). Speeds are shown
rounded to whole km/h everywhere: 128,5008 km/h is "129".

## Not covered

No per-shot position or target: one distance and one target cover the whole
session. Only the goal line and the end boards are targets - not the back of
the net. Checked with a Tk smoke script (simulated clicks on the map, chips,
goal and end boards, radio buttons, typed/invalid distances, the target
reaching `start_recording`, session rows, details, shot pick, play target,
delete, reset, and a window capture to look at the layout) - not yet with a
real recording made through the new controls.

Files: `core/rink.py` (new), `core/session_meta.py` (new), `rink_map.py`
(new), `core/compose.py`, `core/session.py`, `core/pending.py`, `gui.py`,
`tests/test_session_meta.py` (new), `tests/test_compose.py`,
`tests/test_pending.py`. pytest tests: 173 passed.
