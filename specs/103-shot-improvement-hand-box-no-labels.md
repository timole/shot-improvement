# 103 — Shot-improvement: hand boxes, rectangle only (no label text)

## What

`core.pose.draw_palm_boxes()` no longer draws "left hand"/"right
hand" text next to each box - just the yellow rectangle. `PalmBox.label`
still exists and is still set (used elsewhere, e.g. log lines), it's
just not rendered onto the frame any more.

## Why

Direct user feedback, immediately following spec 102's "yellow
rectangles + hand boxes on shot images" work: keep the rectangles,
drop the text.

## Test plan

- `venv\Scripts\pytest tests\` - 100 passed, no test changes needed
  (`test_pose.py`'s draw test only checks the frame was modified at
  all, which the rectangle alone still satisfies;
  `test_composite_into_matches_reference_pipeline_*` in
  `test_compose.py` compares against `draw_palm_boxes` directly, so
  it automatically tracks whatever that function currently does).
- Regenerated and visually confirmed, rectangle-only boxes, no text:
  `recordings/sample-image-annotated.jpg`,
  `-02-annotated.jpg`, `-03-annotated.jpg`, `-04-annotated.jpg` (new
  test photo - a different framing/background than the first three;
  MediaPipe placed one of its two hand boxes visibly wrong there -
  floating over empty space between the two real hands - a model
  accuracy limitation on that particular photo, not a rendering bug),
  and `recordings/shot-improvement-20260914140721-annotated.mp4` +
  its `-shot-01.jpg` through `-shot-05.jpg` (produced via
  `tools/annotate_existing_video.py`, testing the full pipeline -
  clap detection, pose annotation, claps-band video, and the spec 102
  shot images - end to end on a real clip in one run).
