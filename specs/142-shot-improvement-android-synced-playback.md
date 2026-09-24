# 142 — Android: audio, video and spectrogram play back together

Specs 139-141 gave a shot's detail dialog three playback controls that
each lived in their own bubble: a "Toista ääni" button playing audio,
video frame-stepping with no audio at all, and a static spectrogram that
never showed where either one currently was. This spec makes them one
thing: pressing play advances audio, the shown video frame, and a moving
line on the spectrogram together; stepping the video frame by frame (or
jumping ±1s) seeks the audio to match and moves that same line, instead
of only moving the video.

## One playback clock

`ShotDetailDialog` now holds a single `currentTimeS: Double` - the
position everything else is derived from, not each control's own local
state. `MediaPlayer` is prepared once, up front (a `LaunchedEffect`
keyed on `record`, not lazily on the first play tap the way spec 139
first built it), specifically so a frame-step or ±1s press can seek it
even before play has ever been pressed - otherwise the first manual step
would move the video and the spectrogram line but leave the (not yet
created) player silently behind them.

```kotlin
LaunchedEffect(playing, mediaPlayer) {
    val player = mediaPlayer ?: return@LaunchedEffect
    if (!playing) return@LaunchedEffect
    while (true) {
        currentTimeS = player.currentPosition / 1000.0
        delay(33L)
    }
}
```

While playing, the audio clock is the source of truth: this polls
`MediaPlayer.currentPosition` (not a fixed per-frame delay the way
video-only autoplay worked before) since MediaPlayer is what's actually
audible, and both the video frame and the spectrogram line are derived
from wherever it actually is, 30 times a second.

```kotlin
fun seekToS(targetS: Double, durationS: Double) {
    playing = false
    mediaPlayer?.pause()
    val clamped = targetS.coerceIn(0.0, durationS)
    currentTimeS = clamped
    mediaPlayer?.seekTo((clamped * 1000).toInt())
}
```

Every manual control - frame ◀/▶|, ±1s - now calls this instead of
touching its own local frame index: it pauses playback (a manual step
always takes over from auto-play, the same convention spec 140 already
used for its own controls), updates `currentTimeS`, and seeks the
player, so the three never drift out of sync with each other or with
what the next "play" press would actually be audible from. The shown
video frame is a pure function of `currentTimeS`
(`nearestFrame(frameTimesS, currentTimeS)`) rather than its own state -
`VideoFrameBox` lost its play/pause/step controls entirely and is now
just a square image plus the "ruutu N/M" readout, driven from outside.

## A moving line on the spectrogram

```kotlin
private val PLAYHEAD_MARKER_COLOR = Color(0xFFFFFFFF)
...
val markers = fixedMarkers + SpectroMarker(currentTimeS, "", PLAYHEAD_MARKER_COLOR)
```

`SpectroMarker` already supported an arbitrary time/label/colour line
(the live view's own scrolling "Laukaus"/hit markers, spec 138) so no
change was needed to `SpectrogramCanvas` itself - a fourth marker, white
and label-less so it reads as a plain moving line rather than another
named event, just gets added to the same list every recomposition.
Because `currentTimeS` changes every 33ms while playing, this line
visibly tracks playback the whole time, not only at the start/end of a
step.

## Shots with no video

A shot recorded before spec 140, or one where the camera wasn't
available that session, still has no `frameFiles`. Its dialog keeps the
plain "▶ Toista ääni"/"⏸ Pysäytä" button rather than the five-button
video transport (frame-stepping has nothing to step through), but it now
runs through the same `togglePlayback`/polling loop as the video case -
so the spectrogram line still moves during audio-only playback too, a
small generalisation of what was asked but the same mechanism at no
extra cost.

## Verified on the phone

A real OnePlus 10T 5G, on a shot with real video frames on disk from
earlier testing: pressing ▶ showed `MediaPlayer start called` in
logcat, the frame counter advancing (1/60 → 25/60 over about a second)
and the spectrogram's white line moving right along with it, all at
once. Pressing the same button again paused all three together.
Pressing ◀ once while paused moved the video back exactly one frame
(13/60 @ 0,400s → 12/60 @ 0,368s - a single ~32ms step at this clip's
~30fps) and the spectrogram line moved left by that same small amount,
confirming frame-by-frame stepping and the spectrogram move together at
matching granularity, not just on play/pause.

## Not covered

No scrubber/seek bar on the spectrogram itself (tapping the spectrogram
to seek) - only the existing transport buttons drive `currentTimeS`.
Audio/video sync during playback is only as tight as `MediaPlayer`'s own
decode/seek latency and the 33ms poll interval - fine for a ~2s clip
watched on a phone screen, not frame-accurate in a rigorous sense.

Files: `android/app/src/main/java/tech/timolehtonen/shot/MainActivity.kt`
(modified - `ShotDetailDialog` rewritten, `VideoFrameViewer` replaced by
the presentational `VideoFrameBox`), `android/app/build.gradle.kts`
(version bump, 0.5.0/5 → 0.6.0/6). No production logic changed outside
Compose UI wiring, so all 88 unit tests still pass unmodified; installed
and exercised end-to-end on a real OnePlus 10T 5G.
