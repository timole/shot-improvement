# 140 — Android: video alongside a shot's audio

Adds video capture to the live-listening app: while "Aloita kuuntelu" is
running, the back camera records continuously alongside the microphone,
and a shot's detail dialog now shows a square, frame-steppable video clip
below its spectrogram, so the stick's bend at the moment of impact can be
looked at directly instead of only heard.

## Capture

`CameraSession` (new) is the video equivalent of `MicSession`: opened
once when listening starts, delivers frames via a callback on its own
background thread until closed. It uses the plain Camera2 capture-request
API, not the constrained-high-speed session a phone's dedicated
120/240 fps slow-motion mode needs - that mode requires a
MediaCodec-based recording pipeline, not simple frame-by-frame
`ImageReader` capture, which is substantially more machinery than this
feature needs (see "Not covered"). Within that ordinary API it asks for
the **highest FPS range the camera reports**
(`CONTROL_AE_TARGET_FPS_RANGE`, picking the range with the highest
`.upper`) at a small YUV_420_888 size (closest to 480 px wide, so a few
seconds of frames stay comfortably in memory). On the test phone (OnePlus
10T) this picked 352x288 at up to 30 fps - the camera's own ordinary-mode
ceiling.

Each `ImageReader` callback converts the frame from YUV_420_888 (three
possibly-padded, possibly-semi-planar planes) to a packed NV21 byte array
- what `android.graphics.YuvImage.compressToJpeg` expects - stride- and
pixel-stride-aware, so it doesn't assume the planes are tightly packed:

```kotlin
val yRowStride = yPlane.rowStride
if (yRowStride == width) {
    yBuffer.get(nv21, 0, ySize)
} else {
    var dst = 0
    for (row in 0 until height) {
        yBuffer.position(row * yRowStride)
        yBuffer.get(nv21, dst, width)
        dst += width
    }
}
```

`LiveVideo` (new, pure logic, unit tested) is a rolling ~3-second window
of `VideoFrame`s, the video counterpart of the audio side's own rolling
buffers - a 20-minute listening session must not keep every frame the
camera ever produced in memory. 3 seconds gives margin over the 2-second
snippet window plus the detector's own latency. `feed` evicts anything
older than `latest.timeS - windowSeconds`; `snapshotWindow(from, to)`
returns the frames in a time range, oldest first - exactly what a shot's
save step needs.

Both mic and camera open within the same background-thread setup
sequence in `startListening()` and each timestamps its own output as
elapsed time since its own open, which keeps them closely aligned for a
short snippet without needing a shared clock.

**Video is strictly optional, never blocking.** `AndroidManifest.xml`
declares `CAMERA` as a permission but `android.hardware.camera` as
`android:required="false"` - a phone with no camera, or a user who
declines the camera permission, still measures speed from audio alone,
same as before this spec. The permission launcher now requests
`RECORD_AUDIO` and `CAMERA` together
(`ActivityResultContracts.RequestMultiplePermissions()`), but only a
granted microphone gates listening; a declined or unavailable camera
just means `CameraSession.open` returns `null` and the session proceeds
audio-only, logged (`"camera unavailable, continuing audio-only"`) but
not surfaced as an error to the user.

## Saving a shot's video

`ShotFiles.writeFrames` (new) saves a shot's video as
`<stem>-frames/frame_NNNN.jpg` plus a `<stem>-frames.json` sidecar of
each frame's time in seconds relative to the clip's own first frame -
JPEGs, not an encoded video container, since the player steps through
frames one at a time anyway and this needs no encoder/muxer:

```kotlin
fun writeFrames(context: Context, stem: String, frames: List<VideoFrame>): Int {
    if (frames.isEmpty()) return 0
    ...
    for ((i, frame) in frames.withIndex()) {
        val yuv = YuvImage(frame.data, ImageFormat.NV21, frame.width, frame.height, null)
        yuv.compressToJpeg(Rect(0, 0, frame.width, frame.height), 90, out)
        File(framesDir, "frame_%04d.jpg".format(i)).writeBytes(out.toByteArray())
        times.put(frame.timeS - startS)
    }
    ...
}
```

`saveVideoSnippet`, called from the same `persist(record)` step that
already saves the audio snippet, uses the *same* `PREROLL_S`/`POSTROLL_S`
window as the audio (one second either side of the shot) via
`video.snapshotWindow`. If no `CameraSession` opened for this listening
session (`video == null`), it's a no-op - an old shot, or one recorded
audio-only, simply has no frames folder, and `readFrames` treats that the
same as "no video" rather than an error.

## Playback: `VideoFrameViewer`

Added to `ShotDetailDialog`, directly below the existing "▶ Toista ääni"
audio button. Loads the shot's frame files and times off the IO
dispatcher, decodes only the currently-shown frame on demand
(`BitmapFactory.decodeFile`, `remember(frameIndex, frameFiles)` so it
isn't redecoded on unrelated recompositions) rather than holding every
frame's bitmap in memory at once. The square proportion the spec asked
for is `Modifier.fillMaxWidth().aspectRatio(1f)` with
`ContentScale.Crop`, on a black background so a frame that doesn't
exactly fill the square (source is 352x288, not square) still looks
intentional rather than showing a stray edge of background colour.

Controls, a row below a "ruutu 22/60&nbsp;&nbsp;&nbsp;0,702&nbsp;s"
frame-counter/time readout: `-1 s`, `◀` (previous frame), `▶`/`⏸`
(play/pause), `▶|` (next frame), `+1 s`. Play advances one frame at a
time on a `LaunchedEffect(playing, ...)` loop, delaying by that step's
actual inter-frame gap (from the times sidecar, clamped to 10-500 ms so
a corrupt or missing gap can't freeze or spin the UI) rather than a fixed
interval, and loops back to frame 0 at the end. `±1 s` jumps to the frame
whose recorded time is nearest the current time ± 1 second
(`nearestFrame`, linear scan - a few dozen frames, no need for anything
smarter) rather than stepping a fixed frame count, so it stays correct
regardless of the camera's actual FPS. Any manual control (step or jump)
stops playback first, matching ordinary player conventions.

Verified on the phone end-to-end: triggered a shot (`triple_click.wav`
played from the computer), confirmed via logcat and `adb shell ls` that
`shot-<stem>-frames/` held 54 JPEGs and the sidecar's `frameTimesS`
spanned 0 to ~1.76 s; pulled two sample frames and visually confirmed
clean, correctly-oriented, non-corrupted JPEGs (proving the YUV→NV21→JPEG
path). Opened the shot's detail dialog and confirmed by screenshot that
the square video box, frame counter, and control row all render below
the spectrogram and audio button. Exercised every control by simulated
tap + screenshot: ▶ advanced the frame/time readout and swapped its icon
to ⏸; tapping it again paused at a specific frame; ◀ and ▶| moved exactly
one frame each way; `+1 s` and `-1 s` each landed on the frame nearest
±1.000 s from the frame jumped from (0.702 s → 1.698 s → 0.702 s).

## Not covered

- No slow-motion (120/240 fps) mode - see "Capture" above; the camera's
  ordinary API ceiling on the test phone was 30 fps, which shows motion
  blur on a fast stick rather than crisp per-frame bending. A future spec
  could add the constrained-high-speed session and a MediaCodec-based
  recorder if this isn't sharp enough in practice.
- Audio/video sync is "close" (both start within the same setup
  sequence, each timestamps against its own clock) but not
  frame-accurate - not a concern for a ~2-second clip, but not something
  to rely on for exact alignment either.
- No scrubber/seek bar, only frame-step and ±1 s jumps (as specified).
- A shot recorded before this spec, or one where the camera was
  unavailable, simply shows no video section - not flagged as an error,
  since that's expected for most of the app's existing history.
- Not tested with an actual hockey stick/shot - only with synthetic audio
  clicks and the camera pointed at whatever was in front of the phone
  during testing (a wall), so "can the stick's bend actually be seen"
  is unverified in this spec; it depends on real ice-rink lighting,
  distance, and the 30 fps ceiling noted above.

Files: `android/app/src/main/java/tech/timolehtonen/shot/{MainActivity,
ShotFiles,AndroidManifest.xml}` (modified), `android/app/src/main/java/
tech/timolehtonen/shot/{CameraSession,LiveVideo}.kt` (new),
`android/app/src/test/java/tech/timolehtonen/shot/LiveVideoTest.kt`
(new), `android/app/build.gradle.kts` (version bump, 0.3.0/3 →
0.4.0/4). 73 unit tests passed; installed and exercised end-to-end on a
real OnePlus 10T 5G (capture → save → dialog render → every playback
control, all confirmed by screenshot).
