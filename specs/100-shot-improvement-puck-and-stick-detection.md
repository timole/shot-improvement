# 100 — Shot-improvement: puck + stick detection, hand box tweaks

**Mostly superseded by [101](101-shot-improvement-stick-between-hands.md)**:
puck detection/annotation was removed entirely (explicit request), and
the stick's shaft/blade geometry was redesigned to anchor between the
player's two hands instead of hand-to-puck. This doc is kept as the
historical record of the first version; the hand-box-size/English-
label changes below are still current.

## What

- Hand boxes (`core.pose`) are half their previous size (`BOX_SIZE`
  80 → 40), and their labels are English now ("left hand"/"right
  hand" instead of "vasen kasi"/"oikea kasi").
- New `core/stick.py`: detects the puck (black square + black "puck"
  text) and the hockey stick - its blade (yellow square + "blade"
  text) and its shaft (a red curve from the detected hand down to the
  blade).
- New `tools/annotate_image.py`: runs hand + puck + stick detection
  against a single still image, for testing/tuning without recording
  a clip. Used to produce `recordings/sample-image-annotated.jpg`
  from `recordings/sample-image.jpg` - the real test case this was
  built and tuned against.

## Why

The first real step toward actually tracking stick/puck motion
(distinct from spec 098's audio-only shot/hit timing) - this app's
long-stated, still-mostly-unspecced goal.

## Design - classical CV, tuned and validated against one real photo

No trained detector exists for a puck or a stick anywhere in this
app - MediaPipe's PoseLandmarker (already used for hands) only knows
human body landmarks. Every threshold below was tuned and validated
against `recordings/sample-image.jpg`, not guessed, but also not
proven to generalize to very different framing/lighting/distance -
same "state the approximation plainly" spirit as spec 098's clap
detection.

**Puck**: the lowest (closest to camera) small, dark, wide-relative-
to-tall blob in the bottom 30% of the frame. A puck viewed near ice
level foreshortens into a short wide oval (aspect ratio ~1.8-4.0),
distinct in shape from the stick's blade sitting in the same dark
region just above it - picking the LOWEST shape-matching blob (not
just the biggest, which an earlier draft tried and got the fence/
boards instead) reliably separates the two.

**Stick shaft**: traced from the topmost detected hand (reusing
`core.pose`'s already-detected palm position - not a second detection
pass) down to the puck, by searching each image row in a narrow
corridor around the straight line between those two anchors for a
genuine dark feature (the row's darkest point, but only trusted when
(a) the row's background is bright enough to distinguish a dark
object from it at all - skips rows crossing the body/clothing, where
the stick can't be told apart from what's behind it - and (b) the
row's darkest-to-brightest spread clears a minimum contrast, so a
uniform patch with no real feature doesn't get "traced" as an
arbitrary tie-broken pixel). A degree-2 polynomial fit through the two
anchors plus every trusted row's point produces a curve that comes
out close to a straight line when the real data is nearly collinear
(this test photo's un-bent stick) and can flex to follow a genuine
bend where the traced points support one; degree drops to 1 (a plain
line) when there aren't enough trusted points to condition a
quadratic.

**Blade**: not independently detected - a fixed-size box (same size
as the new, halved hand box) at the shaft curve's own bottom anchor,
same "small box at an estimated point" convention `core.pose.
draw_palm_boxes` already uses for hands.

**Puck color** (confirmed with the user - the request's own wording
had "mark it with white square" and "the square is black, too" in
direct conflict): black square, black text, matching the puck's real
color - not white.

## Known limitations (v1, stated plainly)

- Puck search is hardcoded to the bottom 30% of the frame and a fixed
  brightness/size/aspect range - tuned for this one photo's framing
  (subject + puck both near the bottom, ice clearly brighter than the
  puck). A very different camera angle or distance would likely need
  these retuned.
- The shaft trace only works where the stick crosses a brighter
  background than itself (true for this photo's ice-level path, not
  guaranteed in general) - rows crossing the body fall back to
  whatever the polynomial fit interpolates between trusted points,
  not a genuine trace.
- Only one hand was detected in the test photo (MediaPipe found "left
  hand" only) - the topmost-hand anchor logic is exercised by tests
  with synthetic multi-hand input, but not yet against a real photo
  where both hands are actually detected.
- Not wired into the recording pipeline (`compose_annotated_frames`,
  etc.) - this is a standalone tool/module for the single-image test
  the user asked for, not yet a per-frame feature on recorded clips.

## Test plan

- `venv\Scripts\pytest tests\` - 110 passed (10 new in
  `tests/test_stick.py`: puck found/rejected/lowest-wins, stick curve
  anchors and topmost-hand selection, actually follows a real
  synthetic dark shaft rather than drawing a naive straight line
  regardless of image content, draw functions don't crash).
- `tests/test_pose.py`/`tests/test_compose.py` updated for the English
  labels (the two hardcoded "vasen kasi"/"oikea kasi" assertions in
  `test_pose.py`) - `test_compose.py`'s own labels there are arbitrary
  test fixture strings, not tied to the real constant, left as-is.
- Real photo: `venv\Scripts\python tools\annotate_image.py
  recordings\sample-image.jpg` -> `recordings\sample-image-annotated.jpg`,
  visually confirmed: correctly-sized halved hand box labeled "left
  hand"; black puck square + "puck" text at the real puck's location;
  yellow "blade" box where the traced shaft meets the puck; red shaft
  curve from hand to blade, close to a straight line (matching the
  photo's genuinely near-straight, un-bent stick).
