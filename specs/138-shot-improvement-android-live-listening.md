# 138 — Android: live listening, a rink map for the place, and shot history

The Android app (spec 136) no longer records a fixed clip and analyses it
afterward. It now listens continuously: the user picks a shooting place on
an on-device rink map, taps "Aloita kuuntelu", and every shot heard from
then on is reported the moment its hit is heard, with no per-shot action
needed - shoot again and the next speed appears the same way. Every shot is
kept, with its time, so past shots can be browsed. Still fully offline.

## Rink map (`Rink.kt`, `RinkMapView.kt`)

A phone-sized IIHF rink (`Rink.kt`, ported from `core/rink.py`'s geometry)
drawn on a Compose `Canvas` (`RinkMapView.kt`), scaled to the device width
via `size.width / RINK_LENGTH_M` rather than a fixed pixel-per-metre
constant. Unlike the desktop's map, the target is always the end boards -
the phone stands at the shooter and always measures to the far end, never
to a goal, so there is no target picker. The six named places (own goal
line, own face-off spots line, own blue line, centre line, attack blue
line - the default, 22,5 m, matching the original v1 preset - attack
face-off spots line) are dashed lines with a distance chip; tapping a chip,
its line, or within 2 m of it (`SNAP_M`, wider than the desktop's 1,5 m -
touch needs more slack than a mouse) picks that place, tapping anywhere
else on the ice sets the distance to the end boards at that spot, rounded
to 0,1 m. Both goals are drawn for a recognisable rink, but only the end
boards - highlighted in orange - are the actual target. The rink map is
disabled while listening; the place is locked in when "Aloita kuuntelu" is
pressed and cannot change until "Lopeta kuuntelu".

## Streaming detection (`LiveDetector.kt`, `GrowableShortBuffer.kt`)

`Claps.kt`'s detector ran once over a whole recorded clip (global
non-maximum suppression, needing the entire buffer). `LiveDetector` is a
streaming state machine adapted from it, fed 4096-sample chunks as they
arrive from the microphone, that reports events as soon as they can be
confirmed rather than after a fixed duration:

- A window crossing the adaptive threshold opens a **candidate**; its peak
  is finalised `CANDIDATE_FINALIZE_S` (0,15 s) later - long enough to find
  the window's true local max and to give the spectral gate's ±92,9 ms
  window its lookahead, short enough to still feel live - then a
  `minSeparationS` **refractory** period follows, the same neighbour
  suppression as the offline algorithm's NMS, just applied going forward.
  `minSeparationS` still shrinks for a short distance exactly as spec 128
  shrinks it offline (`min(0.45, 0.8 × minHitDelay)`), now load-bearing
  for real since the app supports more than one fixed distance.
- The adaptive threshold's median and the prominence gate's local median
  both come from a rolling 3 s window of RMS values instead of the whole
  clip's; the prominence window is `[-1.0s, +0.15s]` around the candidate
  (the offline ±1,0 s needs future audio that streaming doesn't have yet).
- Pairing is `core.claps.pair_claps`'s greedy rule, applied one clap at a
  time as each is confirmed, plus a wall-clock timeout: a pending shot
  with nothing landing inside its own hit window's upper bound is reported
  unpaired, so the UI is never left waiting for a hit that will not come.
- The wall-of-noise guard (`MAX_CLAPS_PER_SECOND`) drops individual claps
  that push the trailing 1 s rate too high, instead of discarding a whole
  clip retroactively.

`LiveDetector` does no IO - it is fed plain sample arrays and returns
`Event`s, so it is unit-tested (`LiveDetectorTest`) by feeding a
synthetic clip through in realistic 4096-sample pieces (the way a real
`AudioRecord.read()` loop would) and checking against
`Claps.detectClaps`/`pairClaps` run on the same clip at once, plus the
streaming-only behaviour (timeout, the distance-dependent refractory
shrink) that has no offline equivalent. Its audio buffer
(`GrowableShortBuffer`, fixed-size 1 s chunks, no realloc-and-copy) is
never trimmed - a session is capped at 20 minutes
(`LiveDetector.MAX_SESSION_S`), after which listening stops itself.

## Microphone (`MicSession.kt`)

`ShotRecorder`'s fixed-duration `record()` is gone; `MicSession.open()`
does the same source/rate negotiation and AGC/NS/AEC disabling as before,
but `startRecording()`s immediately and returns a session that is `read()`
in a loop on a background thread until the user stops - both impacts of
every shot are offsets into this one continuous stream, so still no wall
clock or A/V sync is needed anywhere.

## History (`ShotHistory.kt`)

Every shot - paired or not - is appended to `shots.jsonl` in the same
recordings folder as before, one JSON object per line: durable the instant
it is written, and a torn write can only ever corrupt its own last line.
Hand-written JSON, not `org.json`: `org.json` is an Android-platform stub
in the plain-JVM unit-test jar (every method throws unless run under
Robolectric or on a device), so using it here would have made this module
untestable without adding a much heavier test dependency; the schema is
small, flat and entirely ours to write and read, so a couple of regexes
cover it (`ShotHistoryTest`, round-tripping through a real temp file).
`Log.w` on the malformed-line path is the same kind of stub - the app
module's `testOptions.unitTests.isReturnDefaultValues = true` (new)
default-values that and any other incidental platform call in a unit
test, without either pulling in Robolectric or mocking every call site.

The screen loads history at startup and shows it newest-first below the
listening controls: date/time, place, and speed (or "—" for an unpaired
shot). A short WAV + JSON sidecar is still saved per shot (shot-to-hit
span ± 1 s), for re-tuning against real recordings via
`tools/analyze_wav.py`, exactly as spec 136 did per full recording -
this is diagnostic capture, not something the user starts or sees a
countdown for, and (like every other recording this app makes) it is
never deleted automatically.

## Spectrogram (`Spectrogram.kt`, `SpectrogramView.kt`)

A spectrogram is shown while listening, and for a saved shot from the
history list, both through one composable (`SpectrogramCanvas`) fed
different inputs:

- **Live**: `LiveSpectrogram`, fed the same chunks as `LiveDetector` from
  the same mic-read loop, computes one FFT column per `FFT_HOP` (128
  samples) as audio streams in and keeps a rolling `maxColumns` window
  (`visibleSeconds`, 6 s - enough to show a shot and its hit together at
  the default 22,5 m). A `LaunchedEffect` polls a snapshot of it every
  100 ms; the columns are drawn as one `Bitmap` stretched across the
  canvas (rebuilding a ~2000-column bitmap from scratch 10x/s measured
  cheap enough not to matter - no profiling beyond that was done). The
  window shown is always `[oldest kept column, now]`, so a shot's marker
  drawn at its own fixed timestamp visibly moves left as `now` advances,
  and disappears once it is more than `visibleSeconds` old - exactly the
  scrolling behaviour asked for, and it falls out of the window
  computation for free rather than needing to be animated explicitly.
- **Static**: tapping a history row opens a dialog that loads that shot's
  saved WAV snippet (`ShotFiles.readWav`, a small reader added alongside
  the existing writer - RIFF chunk-walking rather than assuming the fixed
  44-byte header, PCM16 mono only) off the IO dispatcher and runs
  `Spectrogram.columns()` (the same FFT, non-streaming) over the whole
  clip once. The shot/hit markers use the same maths `saveSnippet` used
  to trim the WAV (`shotT - startS`), so they land in the right place
  without needing the offsets stored separately.

Both colour a dB magnitude against a **fixed** reference level (int16
full scale), not each frame's own max - a live view renormalising itself
every 100 ms visibly flickers in brightness as the max jumps around.

A shot's marker is a vertical line + text tag ("Laukaus", or the hit's
speed / "Osuma"). Found only by testing on the phone (see below): a
single-colour line in the marker's own colour was frequently
unreadable, because it necessarily sits right on top of the loudest
part of the spectrogram, which this colourmap already renders in warm
yellow/orange - the same problem `core/compose.py`'s own labels solve
with a white-on-black outline. Every marker line and text label here
gets the same treatment (a black line/shadow first, the colour on top).

`ShotRecord` gained `snippetFile` (the saved WAV's name) so a history
row can find its own audio; a session's `ShotRecord`s from before this
field existed load with `snippetFile = null` and the dialog shows
"Äänitiedostoa ei löytynyt." instead of a spectrogram.

## Verified on the phone

Built and installed via `adb` (wireless debugging, paired once;
`gradlew installDebug` thereafter) - the first real on-device run of
this whole spec, not just JVM tests. Tested by playing synthetic clicks
through the PC's speakers next to the phone and checking the result on
screen (screenshots pulled via `adb shell screencap`, keeping the mic
permission pre-granted so no dialog blocks the automated loop):

- A real shot/hit pair, played as two clicks spaced for a known speed,
  produced the expected speed within the usual rounding.
- The live spectrogram animates from real captured audio, and shot/hit
  markers appear at the correct, smoothly-scrolling position (confirmed
  by logging each marker's drawn x-coordinate across ~100 successive
  frames - a steady decrease, matching "moves left" exactly - before
  removing the logging again).
- The per-shot dialog opens from a tapped history row and shows that
  shot's own spectrogram with its marker(s), loaded from its saved WAV.
- A single quiet click is genuinely marginal against real room noise -
  it is not always detected, and two clicks closer than about a second
  sometimes only the first registers (a plausible phone-mic AGC ducking
  the second, not reproduced with the offline synthetic tests - a real
  stick-puck impact and a boards impact are louder and further apart
  than this and were not similarly marginal in the sessions run here).
- The phone's own screen lock is unrelated to whether listening keeps
  running (it does, tested across several minutes locked), but froze
  `adb screencap` (returns black - Android blocks capturing the secure
  keyguard) until unlocked.

## Not covered

No manual distance entry on the map screen (tap-only, unlike the desktop's
typed field); no visible warning when the noise guard drops a clap (only
logged); a session's audio buffer, and so its snippet-saving ability, is
held in memory only - nothing is written to disk until a shot's WAV is
saved, so a crash mid-session loses whatever hasn't completed yet, same as
spec 136.

Files: `android/app/src/main/java/tech/timolehtonen/shot/{Rink,
RinkMapView,LiveDetector,GrowableShortBuffer,MicSession,ShotFiles,
ShotHistory,MainActivity,Spectrogram,SpectrogramView}.kt`
(MicSession/ShotFiles new from a split of the old ShotRecorder.kt; the
rest new except MainActivity, rewritten), `android/app/build.gradle.kts`,
`web/android.html`, `android/app/src/test/java/tech/timolehtonen/shot/
{Rink,GrowableShortBuffer,LiveDetector,ShotHistory,ShotFiles,
Spectrogram}Test.kt` (new). 59 unit tests passed; `:app:assembleDebug`
(8,4 MB debug APK); installed and exercised on a real OnePlus 10T 5G.
