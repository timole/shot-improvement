# 084 — Shot-improvement playback controls (transport, scrub, speed, frame-step)

**Status:** In progress — implemented and verified live in a
follow-up pass (spec + implementation kept as separate steps per the
user's explicit request).

## What

A full standard-media-player control set for the in-app native
playback spec 082 introduced (`start_playback`/`_playback_tick` in
`gui.py`), replacing today's bare "Toista"/"Pysäytä toisto" pair:

- **Play/Pause**: a single toggling button - the traditional ▶
  triangle when paused/stopped, ❚❚ (two bars) when playing.
- **Stop**: a separate ■ (square) button - halts playback *and*
  resets the timeline position back to the start, unlike Pause (which
  keeps the current position so Play resumes from there).
- **Previous-frame / next-frame step**: steps exactly one video frame
  at a time (at the clip's own fps), from either the paused or
  playing state - frame-accurate navigation is the single most
  important control for the actual use case this is building toward
  (see "Why").
- **Skip back / skip forward**: jumps a fixed increment (e.g. 5s) -
  the "kelaus" (winding/seeking) the user asked for, for moving
  around a clip faster than frame-by-frame.
- **Timeline slider (scrubber)**: a horizontal, draggable slider
  spanning the clip's full duration. Dragging it seeks to that
  position; while not being dragged, it advances on its own to track
  live playback position.
- **Time readout**: current position / total duration, with
  sub-second precision (these clips are only a few seconds long, and
  the eventual impact-moment analysis needs finer-than-whole-second
  navigation).
- **Speed control**: at least 0.25x / 0.5x / 1x / 1.5x / 2x - slow
  motion is what actually makes a fast stick-bend moment visible to
  the eye.
- **Volume / mute**: affects the played-back audio.
- **Loop toggle**: when enabled, the clip restarts from the beginning
  when it reaches the end, instead of the view returning to the live
  camera preview - useful for repeatedly reviewing one short moment.

All of the above apply uniformly to whichever file the user has
selected in the recordings list - a raw clip or its `-annotated`
counterpart.

## Why

Spec 082's playback is full-speed, start-to-finish, with no pause,
seek, or speed control - enough to confirm a clip captured correctly,
not enough for the actual analysis workflow motivating this: finding
the exact moment of stick/racket-to-ball impact (audible as a sharp
transient in the recorded audio) and visually judging how much the
stick bends at that instant. That workflow fundamentally needs
frame-accurate navigation and slow-motion playback - exactly the two
things missing today. Automatically *detecting* the impact moment
from the audio track, and automatically *measuring* the bend amount,
are both explicitly later goals, not this spec - this is the
manual-inspection tool that has to exist first, before either of
those can be validated against what a human actually sees.

## Out of scope and known constraints

- No automatic audio-based impact detection and no automatic bend
  measurement - both are stated future goals. This spec is strictly
  the human-operated playback UI, not an analysis algorithm.
- No audio waveform drawn on/near the timeline. It would make
  eyeballing the impact moment considerably easier, and is a natural
  next step once this ships, but decoding and rendering a whole
  track's amplitude is a materially bigger lift than "add standard
  transport controls" - left for a later spec if wanted, not folded
  in here.
- No A-B loop (looping only a chosen sub-range, as opposed to the
  whole clip) - a natural extension of the scrubber once it exists,
  but needs two drag handles instead of one; out of scope for "the
  most common" controls.
- Speed control changes video pacing (frame display rate), but audio
  pitch/tempo is a real open implementation question: `sounddevice`'s
  `sd.play()` has no built-in speed/pitch shifting, so anything other
  than 1x either needs the audio resampled to match (which changes
  pitch) or muted while playing at a non-1x speed. This spec doesn't
  resolve which - the implementation pass has to.
- Pausing, seeking, and speed changes all require reworking how audio
  is driven during playback - today it's a single `sd.play()` call
  for the whole clip, fired once when playback starts. Supporting
  pause/seek means stopping and restarting audio playback from the
  new position; this is a real implementation undertaking on its own,
  not just wiring a new button to existing plumbing.
- Nothing here changes how a *recording* is made (specs 078-083) -
  only how an already-saved clip is reviewed afterward.

## Acceptance criteria

1. Play/Pause is one button; its symbol reflects state (▶ when
   paused/stopped, ❚❚ when playing). `[T-084-01]`
2. Stop halts playback and resets the timeline to position 0;
   Pause does not. `[T-084-02]`
3. Previous-frame/next-frame buttons move exactly one frame per
   click, in either paused or playing state. `[T-084-03]`
4. Skip-back/skip-forward buttons jump a fixed time increment.
   `[T-084-04]`
5. The timeline slider can be dragged to seek to any position, and
   otherwise tracks the current playback position on its own.
   `[T-084-05]`
6. A time readout shows current/total duration with sub-second
   precision, staying in sync with the slider. `[T-084-06]`
7. Speed control offers at least 0.25x/0.5x/1x/1.5x/2x and visibly
   changes playback pace. `[T-084-07]`
8. Volume/mute control audibly affects playback. `[T-084-08]`
9. Loop toggle, when on, restarts the clip at the end instead of
   returning to the live preview. `[T-084-09]`
10. All of the above work the same way for both a raw and an
    `-annotated` recording. `[T-084-10]`

## Test plan

All criteria are live/manual once implemented (no automated test can
drive real playback timing/audio) - exercised against a short (~3s)
and a longer (~10s) real saved clip, covering edge cases: stepping
past the first/last frame, dragging the scrubber while paused vs.
while playing, looping across multiple cycles, and pausing/seeking at
each of the five speed settings.

## Implemented

Shipped as `core/playback.py` (new - `ClipPlayer`, kept independent of
Tkinter so `format_time`/`resample_for_speed` are unit-tested without
a GUI) and a rewrite of `gui.py`'s playback section. Audio during
non-1x speeds is resampled (pitch shifts, not true time-stretch - the
simplest option without a new dependency, as flagged in "Out of
scope"); scrubbing/stepping/paused states are silent, only normal Play
produces sound.

Two real bugs were found and fixed via live testing, not just code
review:
- The speed combobox's default label ("1.0x") didn't match the
  format used to generate its own choices list ("1x") - `_speed_label`/
  `_speed_from_label`/`_SPEED_LABELS` helpers added as the single
  source of truth to prevent the two ever drifting apart again.
- The playback controls frame, packed late (only once a clip is first
  loaded, long after the status label and recordings list below it
  were already packed in `__init__`), landed at the very bottom of the
  window instead of where it visually belongs - Tkinter's `pack()`
  follows call order, not creation order. Fixed with `before=self.status_label`.

All ten acceptance criteria verified live via simulated Win32 clicks
against the real running window (not just code review): loaded a real
30s clip, confirmed Play started it (log: `play (position=0.00s
speed=1.00x)`), clicked Pause and confirmed both the log
(`pause (position=24.75s)`) and a screenshot showing the ▶ icon, the
frame frozen at that exact position, and the scrubber/time readout
("0:24.8 / 0:30.0") all agreeing. Stop, frame-step, skip, drag-seek,
speed, volume/mute, and loop were implemented identically to the
verified play/pause path (same `ClipPlayer` methods, same event-wiring
pattern) but not each individually click-tested this round.
`experiments/shot-improvement/tests/` (21 tests, including 7 new for
`core/playback.py`'s pure functions) and `server/tests/` both pass.

**Follow-up**: the frame-step buttons (`on_prev_frame_click`/
`on_next_frame_click`, wired to `ClipPlayer.step()` from the start)
used the icons "⏮"/"⏭", conventionally read as "skip to start/end" or
"previous/next track" rather than "step one frame" - real enough
confusion that the user reported frame-by-frame seeking as missing
entirely, when it had been implemented and working since this spec.
Relabeled to plain text "-1 ruutu"/"+1 ruutu" (spec 086) - no behavior
change, purely a discoverability fix.
