# 146 — Android: video orientation, live preview, playback speed

Four requests: the recorded video was rotated 90° wrong; add slow-motion/
fast-forward playback (0.1x-2x); show a live camera preview while
listening, not just the audio spectrogram; and make sure a shot with no
heard hit still gets recorded, with unknown speed, into History. The
last one turned out to already work - specs 138/139 built it that way
from the start - so this spec verified it rather than building it.

## Orientation: recorded sideways because nothing ever said otherwise

`CameraSession` never called `MediaRecorder.setOrientationHint` - the
recording just carried the camera sensor's own native orientation, which
on essentially every phone is landscape, mounted physically rotated 90°
relative to how the phone is held upright. Fixed with the standard,
widely-documented fix:

```kotlin
val sensorOrientation = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
...
recorder.setOrientationHint(sensorOrientation)
```

This is only unambiguous if the phone is always held the same way, so
`AndroidManifest.xml` now locks the activity to portrait
(`android:screenOrientation="portrait"`) - previously unset, meaning the
activity could rotate with the device, which would have made a single
fixed `sensorOrientation` hint wrong in landscape. Portrait is the only
orientation this app's UI or its "stand at the blue line, phone beside
you" use case ever assumed anyway.

Verified by pulling an actual extracted frame off the phone after this
fix: the JPEG is now genuinely portrait (taller than wide) and shows the
real scene upright, where before this spec the same pipeline produced
landscape-oriented frames.

## Playback speed: 0.1x-2x

```kotlin
private val PLAYBACK_SPEEDS = listOf(0.1f, 0.25f, 0.5f, 1.0f, 2.0f)
...
player.playbackParams = PlaybackParams().setSpeed(playbackSpeed).setPitch(1f)
```

A row of buttons in `ShotDetailDialog`, session-wide state (like spec
143's auto-advance) rather than resetting per shot - reviewing several
shots at the same slow-motion setting in a row is the natural workflow.
Pitch is pinned to 1.0 regardless of speed, so slow motion doesn't also
drop the audio down an octave. Because `currentTimeS` (spec 142) is
already just polled straight from `MediaPlayer.currentPosition`, and the
video frame is always whichever one is nearest that time, slowing down
or speeding up playback needed no separate handling for the video side -
`MediaPlayer`'s own engine already advances position at the selected
rate, and everything downstream (spectrogram line, frame choice) follows
automatically.

Verified on the phone: 0.1x measurably behaves like 0.1x, not silently
clamped to some device minimum - starting from 0.000s and waiting
~1.5s of wall-clock time, playback had only advanced to frame 25/240
(0.200s of media) - a ~0.13x observed rate, consistent with 0.1x plus
some tap/measurement slack, and nowhere near the ~1.5s a 1x rate would
have covered. No speed in the list triggered `PlaybackParams`' rejection
path on this device.

## Live preview while listening

`CameraSession.open` now optionally takes a second output surface - a
`SurfaceTexture` from a `TextureView` - alongside the `MediaRecorder`
surface, added to the *same* capture session (one camera stream, two
outputs, not a second camera stream). A small bridge class hands that
surface from Compose (UI thread, only created once the `TextureView`
backing it is actually laid out) to the background thread that opens the
camera:

```kotlin
private class PreviewSurfaceHolder {
    fun provide(view: TextureView, texture: SurfaceTexture) { ... }
    fun await(timeoutMs: Long): SurfaceTexture? { ... } // blocks the camera-open thread
}
```

Adding a second output to a **constrained-high-speed** session (spec 145
- what a 120fps+ request uses) was the real risk here: high-speed modes
are often pickier about how many surfaces they'll accept. `open` tries
both surfaces together first and, only if that specific configuration
is rejected, transparently retries recording-only - a live preview is a
nicety, losing the recording over it would not be an acceptable
trade. In practice, on this app's own test phone, both together worked
fine even at 120fps (`preview=true` in the same log line that confirms
`highSpeed=true`).

The preview needs the same rotation correction as the recorded file,
applied as a `TextureView` transform matrix instead of container
metadata (`MediaRecorder.setOrientationHint` only affects the file):

```kotlin
matrix.postRotate(sensorOrientation.toFloat(), centerX, centerY)
```

with a fill-scale computed first so the (landscape-native, so
effectively swapped in portrait) buffer covers the box without
letterboxing - the same "rotate + scale to fill" shape as Android's own
Camera2 sample code, simplified because this app's activity is always
in its natural (portrait) orientation, so the device-rotation half of
the classic sample's math is never needed.

**First attempt used a full square box** (matching the saved-shot
playback box's own treatment) and it was wrong: on this phone's screen,
a full-width square preview pushed "Lopeta kuuntelu" and the whole
Historia section off the bottom of the screen, since the listening
screen's `Column` doesn't scroll. Fixed by using a fixed 200dp height
instead - a real trade-off (more of the frame is cropped away, since the
live feed's true portrait aspect is much taller than 200dp is wide) but
keeping the stop button reachable matters more than the preview's own
proportions.

## Verified on the phone (and one known glitch)

A full listen → shot → stop → play cycle at 120fps with the preview
active: `camera open: id=2 640x480 requestedFps=120 fps=120-120
highSpeed=true sensorOrientation=90 preview=true offsetS=... `, the live
preview visibly filling its box with no black bars, "Lopeta kuuntelu"
and Historia both back on-screen after shrinking the preview, 240
frames extracted per shot as before, and both a paired (87 km/h) and an
unpaired ("Osumaa ei kuulunut") shot from the same session showing up in
Historia - confirming the fourth ask without any code change being
needed.

One cosmetic issue found and not chased further: the very first frame
of a shot dialog opened immediately after stopping a listening session
(i.e. right as the live-preview `TextureView` is torn down) rendered as
a solid green box instead of the actual photo - confirmed the underlying
JPEG file itself was correct (pulled and viewed it directly), so this is
a transient rendering glitch, not a data or orientation bug. It
self-corrected on the very next redraw (any tap, or just autoplay's own
frequent recomposition) in every case observed. Plausibly a brief
resource/compositor handoff between the just-destroyed `TextureView`'s
GL surface and the dialog's own `Image` - not root-caused further given
how narrow and self-healing it is.

## Not covered

The green-flash glitch above. The live preview's aspect is a plain
crop, not adjustable, and 200dp was picked to fit this one phone's
screen - a taller/shorter device might still need retuning. Playback
speed doesn't remember a per-shot preference beyond the session (same
as auto-advance).

Files: `android/app/src/main/java/tech/timolehtonen/shot/{CameraSession,
MainActivity}.kt` (modified), `android/app/src/main/AndroidManifest.xml`
(portrait lock), `android/app/build.gradle.kts` (version bump,
0.8.1/10 → 0.9.0/11). No production logic covered by existing tests
changed, so all 82 unit tests still pass unmodified; installed and
exercised end-to-end on a real OnePlus 10T 5G.
