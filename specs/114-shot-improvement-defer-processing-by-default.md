# 114 — Shot-improvement: "Vain nauhoitus" checked by default

## What

The GUI's "Vain nauhoitus (käsittele myöhemmin)" checkbox now defaults
to checked, instead of unchecked.

## Why

Measured directly on this hardware (3.83GB RAM): a 3-recording burst
test, each recording started right after the previous one (matching
real usage), showed that a new recording overlapping a previous one's
still-running background annotation pass isn't just slower - it can
fail outright. Recording 1 (no contention) captured cleanly at 28.5fps
real throughput; recording 2 (one background pass still running)
captured **0 of the expected ~330 frames** - every single camera read
failed for the full 10s window; recording 3 (two background passes
overlapping) degraded to 10.6fps with large stalls. A resource sampler
running in parallel measured free memory dropping as low as ~97MB
during the burst (from a ~750MB idle baseline out of 3.83GB total) -
the background pass's second MediaPipe pose model plus per-frame
annotation work is enough to starve the live camera read itself on
this little headroom.

Deferred processing (spec 093) sidesteps this entirely: capture does
only a camera read + one disk write per frame, nothing else, until
"Käsittele odottavat" is clicked - explicitly why that mode exists.
Given real recordings can now silently come out with a handful of
frames or none at all under the OLD default, checked-by-default is the
safer choice for this hardware, not just a preference.

## Files

- `gui.py` - `defer_processing_var`'s initial value, `False` -> `True`.

## Test plan

- `venv\Scripts\pytest tests\` - 112 passed (this is a UI default, not
  exercised by the test suite).
- The burst test that motivated this (3 recordings, live preview
  between them, via a real Tkinter window driving
  `core.session.LiveSession` the same way `gui.py` does) is not
  automated - it was run manually against real hardware; see the
  numbers above.
