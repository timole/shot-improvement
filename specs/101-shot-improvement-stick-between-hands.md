# 101 — Shot-improvement: stick shaft between the two hands, puck removed

## What

Redesign of spec 100's stick detection, on direct feedback after
seeing that first version's result:

- **Puck detection/annotation removed entirely** - no square, no
  "puck" text, no `detect_puck`/`draw_puck`/`PuckDetection` code.
- **Colors swapped**: the shaft is now yellow (was red), the blade is
  now red (was yellow) - box + "blade" text, same as before.
- **The shaft is anchored between the player's two hands**, not
  hand-to-puck - "the player holds the stick with the top hand and
  the lower hand holds the stick, too."
- **The blade is a traced extension past the lower hand** - continues
  the shaft's own established direction and real pixel content
  downward (no longer anchored to a detected puck, which no longer
  exists), until the trail is lost or a row cap is hit.

## Why

Direct user feedback on spec 100's first result, re-tested against
the same `recordings/sample-image.jpg` plus a second photo,
`recordings/sample-image-02.jpg`.

## The "always two hands" problem, and how it's solved

`core.pose`'s existing hand-box logic only draws a box when a
landmark's MediaPipe-reported visibility clears `VISIBILITY_THRESHOLD`
(0.5) - in `sample-image.jpg`, only the "left hand" cleared that
(0.96+); the "right hand" (the real top hand, gripping higher up near
the head, partly occluded) sat just under it (0.45-0.47), close enough
that MediaPipe clearly still had a good estimate of where it was, just
not confident enough to count as "definitely visible."

Since the request is "the stick should ALWAYS be annotated between
the two hands," relying on the confidence-filtered hand list isn't
enough - a single genuinely one-handed grip is not the normal case
this app cares about, and MediaPipe already estimates a position for
every one of its 33 landmarks regardless of confidence (it never
returns "missing," only "less sure"). New `core.pose.
raw_palm_positions()` computes both hand centers the same way
`palm_boxes_from_landmarks()` does, but WITHOUT the visibility filter -
used only to anchor the stick, never to decide what gets drawn as a
green hand box (that stays exactly as before, spec 100's halved size,
English labels). `PoseDetector.detect_with_raw_hands()` returns both
the filtered boxes and the raw positions from a single underlying
model call, so nothing runs pose inference twice per frame.

As long as any person is detected at all, this always yields exactly
2 hand positions (MediaPipe's model always outputs all 33 landmarks
per detected person) - `core.stick.detect_stick()` sorts them by y and
uses the higher one as the top-hand anchor, the lower one as the
bottom-hand anchor, regardless of which is anatomically "left" or
"right."

## Blade: continuing the trace past the lower hand

Spec 100's blade was anchored to a separately-detected puck position,
now removed. The replacement reuses the exact same row-by-row
dark-feature tracer the shaft already uses (`core/stick.py`'s
`_trace_row`/`_fit_curve`, shared by both), just continuing past the
bottom hand along the direction the shaft curve's own last two points
already established, instead of stopping there.

**A real bug found and fixed while validating against the actual
photo**: the first version of this extension gave up after 4
consecutive untrustworthy rows. On the real photo, the few rows
immediately below the lower hand cross the wrist/forearm - not yet
bright ice - so it gave up almost immediately (`blade_center` landed
just 2px below the hand, visibly wrong). Fixed by NOT stopping early
at all: the tracer now searches the entire capped row range
(`BLADE_MAX_EXTENSION_ROWS = 250`, raised from an initial 120 that
was also too low - the real photo needed ~145px) regardless of gaps,
and takes the LAST trustworthy point found as the blade end. This is
safe against drifting past the real blade: once past the genuine dark
object, the remaining rows are uniform bright ice, which fails the
existing minimum-contrast check (`MIN_CONTRAST`), so no further points
get recorded there - the last real trustworthy point stays close to
where the actual dark region (the blade sitting on the ice) ends.

If nothing trustworthy is found at all (extension immediately loses
any dark feature, no clean ice-level path exists in the frame), a
short fixed-length stub (`BLADE_STUB_LENGTH = 20`) in the established
direction still renders - "the stick should always be annotated,"
even in the degenerate case.

## Test plan

- `venv\Scripts\pytest tests\` - 107 passed. `tests/test_stick.py`
  rewritten for the new two-hands API (shaft spans exactly between
  the given hand positions regardless of input order; blade always
  extends past the bottom hand even with nothing to trace, per the
  stub fallback; a real dark shaft/blade bend in synthetic test
  images is actually followed, not ignored in favor of a naive
  straight line) - the old puck-specific tests were removed along
  with the code they tested.
- Real photos, via `tools/annotate_image.py`:
  - `recordings/sample-image.jpg` (one confidently-visible hand, one
    just under the visibility threshold): yellow shaft correctly
    spans both hands (visibly connecting the green hand box up toward
    the head, where the low-confidence hand actually is); red blade
    extension correctly reaches down to the puck/ice contact point
    (blade_center y=327.9, matching spec 100's independently-detected
    puck position of ~342 closely); no puck square/text anywhere.
  - `recordings/sample-image-02.jpg` (both hands confidently
    detected, close together): very short yellow shaft (hands are
    close together in this photo) followed by a red blade curve that
    visibly follows a real bend in the stick's actual on-ice path,
    not a straight line - confirms the curve-fitting genuinely
    responds to image content, not just interpolating between fixed
    points.
- Not addressed this pass: the "blade" text label runs slightly past
  the bottom edge of the frame on `sample-image-02.jpg` (the blade
  lands close to the image's own bottom edge there) - a minor
  cosmetic edge-clamping gap, not a functional one.
