# 121 — GUI: shots list in the left column, recordings list 6 rows

## What
- The "Laukaukset:" block (speed label, "Toista raakakuvaa" + speed
  picker, shots list, "Takaisin livekuvaan") moves from under the video
  preview to the left column, packed just above the recordings list
  (`before=self.list_frame`). The video preview stays alone in the
  right/center column.
- The recordings list shows 6 rows at a time (`RECORDINGS_LIST_ROWS`,
  was 8); older recordings scroll, none are hidden or deleted.

## Files
`gui.py`.

## Tests
`venv\Scripts\pytest tests\` - 112 passed (layout not covered by tests).
