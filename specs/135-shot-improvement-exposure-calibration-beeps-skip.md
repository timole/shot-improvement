# 135 — Exposure calibration during the countdown, beep count, ±1 s buttons

## Exposure calibration when "Tallenna" is pressed
Some clips came out too dark (the fixed exposure -11 suits bright sun, not
every scene). Pressing "Tallenna" now calibrates the manual exposure while the
beeps play: every 250 ms `LiveSession.auto_exposure_step()` compares the live
picture's mean brightness (every 8th pixel, luminance-weighted) with a target
of 118/255 and nudges the exposure by `0.8 * log2(target / current)` (limited
to +-1.5 per step; brightness roughly doubles per +1 on DirectShow's log2
scale), waiting 5 frames after each change for the camera to apply it. The
range is -11 (driver minimum) to -6 (~15.6 ms, the longest that still fits a
30 fps frame), and the value stays fixed once recording starts, so frame rate
is unaffected. The calibrated value stays for the live preview until the next
calibration; "Palauta oletukset" puts it back to -11.

## Beeps
"Piippaukset:" next to "Kesto (s):" (default 3, 1-10): that many beeps, one per
second, then recording starts (the countdown is as long as the beep count, so
there is time to calibrate). A near-silent 1 ms tone plays first and the
countdown starts 300 ms later: the first beep was being swallowed while the
audio output woke up (only two of three were heard). That cause is my best
explanation, not proven.

## +1 s / -1 s
Two new buttons in the saved-clip transport next to the 5 s ones ("-1s",
"+1s"; `SKIP_SHORT_SECONDS`).

## Annotated videos of today
All of today's annotated clips that still had the old 640x640 layout (black
timestamp band) were regenerated as 640x520 (video + spectrogram) from the
saved raw frames, with their "-hands.json" files, and re-uploaded to Azure:
20260921070521, 070718, 093054, 094004, 095050, 095913, 101740, 102339
(102548 and 103414 were done before).

Files: `core/recorder.py`, `core/session.py`, `core/playback.py`, `gui.py`,
`tests/test_auto_exposure.py`. `pytest tests`: 128 passed.
