# 078 — Shot-improvement hello world

**Status:** Removed — see the note at the end of "Implemented". The
keystroke/fingertip-tap use case this spec describes was cleaned out
of the UI; spec 079's body/pose use case is now the only one on the
page. Kept here as the historical record of what was built and why.

## What

A new experiment at `/shot-improvement`: a browser page that grabs this
laptop's webcam (Logitech, USB) and microphone (Jabra) via
`getUserMedia` as soon as the page loads (not on the first click), and
keeps that stream running continuously as a live top-of-page preview.
Clicking "Nauhoita 3 sekuntia" (disabled until the stream is ready)
just starts `MediaRecorder` on the already-open stream and records a
fixed 3-second clip, and
plays it back with audio. While recording, keydown events are also
captured with their timestamp relative to recording start, and listed
below the played-back clip. A text field below the video gives the
user something to type into during the recording.

During the 3 seconds, the page also runs Google's MediaPipe Hands
model (`HandLandmarker`, loaded client-side from a CDN, no server
round-trip) on the live camera feed, watches the ring-finger and
pinky fingertip landmarks, and draws a green box over a fingertip the
moment its downward velocity crosses a threshold ("a tap"). Each
detected tap is logged with its own timestamp, and a comparison table
matches every "o"/"p" keystroke (the touch-typing keys pressed with
the ring finger and pinky, respectively) against the nearest detected
tap to show whether the finger and timing agree.

## Why

First spike toward a not-yet-specified "shot improvement" feature —
proves the browser can reliably grab this laptop's specific camera/mic
combination and line up a keyboard-timed event stream against a
recorded video/audio clip, before any real feature is specced on top
of it. The fingertip-tap detection is itself a spike for a later,
unrelated need: recognizing the moving tip of a padel racket or a
hockey stick blade in a short (3-10s) clip. A hand-landmark model
(MediaPipe Hands) was chosen over simpler frame-differencing/motion-
blob tracking because this spike specifically needs to tell the ring
finger and pinky apart, which motion-diffing alone can't do — the
tradeoff being that this particular model is hand-specific and won't
carry over to the racket/stick case, where a generic motion/object-
tracking approach will likely be needed instead.

## Out of scope and known constraints

- No server-side storage of the keystroke log or detected taps —
  those stay in the browser tab only. The raw video clip IS saved
  server-side now (`experiments/shot-improvement/recordings/`, see
  acceptance criterion 10) — the one deliberate exception to "nothing
  leaves the browser," since a plain webpage can't write to an
  arbitrary local folder on its own. This only does something useful
  against the local dev server (this laptop); against a deployed
  instance the file would land in that server's own, ephemeral,
  filesystem, not the visitor's machine. `recordings/` is gitignored.
  No access-code gate on the endpoint (nothing sensitive is exposed by
  it, only written), but the filename is strictly regex-validated
  against the pattern the frontend actually generates and the decoded
  size is capped, to rule out path escapes or unbounded writes.
- No device-picker UI. The page best-effort matches devices whose
  label contains "Logitech" (camera) or "Jabra" (mic) via
  `enumerateDevices`, falling back to the browser's own default
  camera/mic when neither is present (e.g. testing on a different
  machine).
- Camera/mic access requires a secure context (https, which
  ai.timolehtonen.tech already is) and an explicit browser permission
  grant every visit (no permission persistence handled here).
- Fixed 3 seconds, not configurable — deliberately minimal; a real
  duration/trigger/storage model is deferred to the next spec.
- The tap-detection threshold (downward fingertip velocity) is
  untuned and deliberately loose — this is a rough spike, not a
  calibrated instrument. It only looks at the ring finger and pinky
  (landmarks 16/20), not the full hand.
- The annotated (green-box) clip is a second, separate `MediaRecorder`
  capture of the same overlay canvas the live boxes are drawn to
  (`canvas.captureStream()` + the original audio tracks), recorded in
  parallel with the raw clip — not a re-processing pass over the raw
  recording afterwards.
- If the MediaPipe model fails to load (offline, blocked CDN, no
  WebGL), recording still works; the page just skips detection and
  says so.
- All detection here runs on-device (local inference, no video/audio
  data leaves the browser) — only the ~5-10MB model file itself is
  fetched once from a CDN on page load. A server/cloud-run model as an
  alternative or complement to the on-device one is a real option for
  later, not attempted here.
- The camera/mic stream is opened once, on page load, and kept running
  for as long as the page stays open — unplugging/replugging a device
  (e.g. swapping to the laptop's built-in camera/mic and back) is only
  picked up on the *next full page load*, not live while a stream is
  already open (no `devicechange` handling here).

## Acceptance criteria

1. `GET /shot-improvement/` returns a page with a "Nauhoita" (record)
   button. `[T-078-01]`
2. Clicking it requests camera+mic permission, then records ~3
   seconds, preferring any device whose label contains "Logitech"
   (video) or "Jabra" (audio). `[T-078-02]`
3. After the 3 seconds, a `<video controls>` element plays back the
   captured clip with audio. `[T-078-03]`
4. Any key pressed during the 3-second window is listed below the
   video with its timestamp (seconds, relative to recording start).
   `[T-078-04]`
5. The page is mounted at `/shot-improvement/` alongside the other
   experiments. `[T-078-05]`
6. A text field is shown below the video, focused automatically once
   recording starts, so keys can be typed into it during the window.
   `[T-078-06]`
7. While recording, a green rectangle is drawn over a detected
   fingertip (ring finger or pinky) whenever it moves down quickly
   ("a tap"). `[T-078-07]`
8. After recording, a table compares each "o"/"p" keystroke against
   the nearest detected tap (finger, time difference, match/no-match).
   `[T-078-08]`
9. Under "Videolta tunnistetut sormen liikkeet", a second
   `<video controls>` plays back the same clip with the green tap
   boxes burned into the picture (audio included), so the detections
   are re-watchable, not just a live flash during recording.
   `[T-078-09]`
10. The raw clip is saved into this repo, at
    `experiments/shot-improvement/recordings/shot-improvement-<YYYYMMDDHHmmss>.webm`
    (timestamped to the recording's wall-clock start), via
    `POST /api/shot-improvement/save-recording` — the one endpoint this
    experiment has, since a browser can't write to an arbitrary local
    folder on its own. Falls back to a plain browser download (to the
    Downloads folder) if that request fails. `[T-078-10]`
11. Camera/mic deviceIds resolved once (Logitech/Jabra match) are
    cached in `localStorage`, so a later click/visit opens the
    hardware directly instead of repeating the slower
    open-enumerate-reopen dance. `[T-078-11]`
12. The camera/mic stream opens automatically on page load (the "top
    video"), before any click; the "Nauhoita" button stays disabled
    until it's ready, and clicking it starts recording immediately
    with no further permission wait. `[T-078-12]`
13. If neither a Logitech camera nor a Jabra mic is present (e.g. only
    the laptop's built-in devices are connected), the page falls back
    to the browser's own default camera/mic without erroring.
    `[T-078-13]`

## Test plan

- `[T-078-01]`, `[T-078-05]`, `[T-078-06]` offline:
  `TestClient(app).get("/shot-improvement/")` in
  `server/tests/test_main.py` asserts 200 and "Nauhoita" in the body.
- `[T-078-02]`–`[T-078-04]`, `[T-078-07]`–`[T-078-13]` live/manual:
  open `https://ai.timolehtonen.tech/shot-improvement/` (or
  localhost) with the Logitech camera and Jabra headset connected,
  confirm the top video starts live on its own and the button enables
  once it does, click record, type "o" and "p" a few times into the
  text field during the 3 seconds, verify playback has both video and
  audio, the keystroke list and detected-tap list both show entries,
  the comparison table shows plausible matches, and a `.webm` file
  lands in the downloads folder named with the recording's start time
  (down to the second). Unplug the Logitech/Jabra, reload the page,
  confirm it falls back to the laptop's own camera/mic; replug them
  and reload again, confirm they're preferred again.

## Implemented

Shipped as `experiments/shot-improvement/web/index.html` (plain
HTML/CSS/JS, no build step, no backend route beyond the static mount;
MediaPipe Hands loaded client-side from `cdn.jsdelivr.net`, model
weights from `storage.googleapis.com`), mounted at `/shot-improvement`
in `server/main.py` next to the other `StaticFiles` mounts.
`[T-078-01]`, `[T-078-05]`, `[T-078-06]` verified via
`server/tests/test_main.py::test_shot_improvement_mount`.
`[T-078-02]`–`[T-078-04]`, `[T-078-07]`–`[T-078-08]` need a live manual
check on the laptop with the actual devices attached — not yet run.

**Removed:** the entire keystroke/fingertip-tap use case (button,
text field, keys/taps/comparison UI, `HandLandmarker` loading and
detection loop, the `saveMessage`/keydown-listener wiring for it) was
deleted from `experiments/shot-improvement/web/index.html` at the
user's request, to keep the page focused on spec 079's body/pose use
case alone. The shared infrastructure this spec introduced (always-on
stream, cached preferred devices, save-to-repo endpoint, raw+annotated
dual recording) was kept and now serves spec 079 instead. The
`shot-improvement-pose-` filename infix that once distinguished the
two use cases' saved clips was also removed along with it — the save
endpoint's filename pattern reverted to plain
`shot-improvement-<YYYYMMDDHHmmss>.webm`, since only one use case
remains to generate it.
