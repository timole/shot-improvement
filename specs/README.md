# Specs

A spec-driven workflow: every feature gets its own numbered markdown
file before implementation. Implementation follows the spec, and the
spec's Status is updated once the feature is in production.

## Format

Every spec file uses exactly this section set:

```markdown
# <feature name>

## What
One or two paragraphs: what is built, what the public interface is.

## Why
The need it answers. Cite the failure it prevents where one is known.

## Out of scope and known constraints
What this spec deliberately does not cover, and external quirks it
must tolerate (rate limits, blocks, missing data).

## Acceptance criteria
Numbered. Each criterion states input → observable output, and names
the test id that verifies it, e.g. `[T-search-03]`.

## Test plan
For each test id: fixture used (offline) or live target, the exact
assertion, and whether it is offline or live/opt-in.

## Implemented
Left empty until done. Then: what was built, which criteria were
verified how, and any criterion that had to change and why.
```

## Structure

- One file per feature: `NNN-short-name.md`
- Status lifecycle: **Draft → In progress → Done**
- Numbering starts at 078, not 001: this repo was split out of the
  `ai-timolehtonen-tech` monorepo, where specs 078–087 were written
  under that repo's own shared numbering (001–...). Kept as-is here
  rather than renumbered, to avoid rewriting every in-file
  cross-reference between these specs.

## Specs

| # | Name | Status |
|---|---|---|
| [078](078-shot-improvement-hello-world.md) | Shot-improvement hello world (3s video+audio capture, keystroke log) | Removed (see 080) |
| [079](079-shot-improvement-body-pose.md) | Shot-improvement body/pose tracking, browser version (palm box via MediaPipe PoseLandmarker) | Removed (see 080) |
| [080](080-shot-improvement-native-python.md) | Shot-improvement native Python app (camera/mic capture + PoseLandmarker, no browser) | In progress |
| [081](081-shot-improvement-native-gui.md) | Shot-improvement native GUI (Tkinter: live annotated preview, record button, recordings list with play/delete) | In progress |
| [082](082-shot-improvement-gui-controls-and-playback.md) | Shot-improvement GUI controls + native playback + annotation-bug fix (device dropdowns, duration spinbox, in-window playback, fresh-detector-per-recording fix) | In progress |
| [083](083-shot-improvement-logging-and-freeze-fix.md) | Shot-improvement logging + a real ~12s freeze fix (background-thread encoding) + a measured PNG→BMP throughput win (6.6→9.3fps) | In progress |
| [084](084-shot-improvement-playback-controls.md) | Shot-improvement playback controls (play/pause/stop, frame-step, scrub, speed, volume, loop) — toward manually inspecting stick-bend at impact | In progress |
| [085](085-shot-improvement-spectrogram-and-background-removal.md) | Shot-improvement spectrogram panel and background removal | In progress |
| [086](086-shot-improvement-performance.md) | Shot-improvement performance: GPU check, deferred pose inference | In progress |
| [087](087-shot-improvement-web-gallery.md) | Shot-improvement web gallery (Google Sign-In gated web gallery, synced from the laptop) | In progress — see README.md's "Web gallery" section; the server side that served this was removed when this repo split out, so the gallery currently has no hosting |
| [088](088-shot-improvement-60fps-720p.md) | Shot-improvement real 60fps capture at 1280x720 (fixed camera device match + MJPG negotiation) | Done |
| [089](089-shot-improvement-gui-recording-throughput.md) | Shot-improvement GUI recording throughput (skip live preview + tick pacing while recording) + no console windows during ffmpeg | Done |
| [090](090-shot-improvement-progress-percentages.md) | Shot-improvement progress percentages for annotating, spectrogram, ffmpeg encoding, and cloud upload (+ a real ffmpeg stdout/stderr pipe deadlock fix found along the way) | Done |
