# 144 — Android: why 60fps isn't actually available, and a real bug fixed along the way

Prompted by a discrepancy: NanoReview's OnePlus 10T 5G spec page lists
"1080p video recording up to 60FPS" and even "480 FPS (720p) slow
motion", while this app's own Settings screen (spec 141) showed 60/120/
240 all disabled with "ei tuettu tällä laitteella" (not supported on
this device). Investigated on the real phone rather than guessing.
Found and fixed a real camera-selection bug along the way, but the
underlying reason 60fps+ isn't usable turned out to be a limitation of
this app's own capture technique, not of the phone.

## First finding: the app was asking the wrong camera

`adb shell dumpsys media.camera` showed this phone exposes **six**
back-facing camera entries, not one - a phone with three physical lenses
(main/ultrawide/macro) plus logical (fused) camera IDs on top of them is
normal for Camera2. `CameraSession.pickCameraId` picked
`cameraIdList.firstOrNull { LENS_FACING_BACK }` - whichever one
Camera2 happened to list first, with no guarantee that's the capable
one. Confirmed directly by logging every back-facing ID's own
characteristics:

```
candidate id=0 normal=[...up to 30fps] highSpeed=[]
candidate id=2 normal=[...up to 30fps] highSpeed=[120,120][240,240][480,480][30,240][30,480][30,120]
candidate id=3 normal=[...up to 30fps] highSpeed=[]
```

Camera id 0 (the one spec 140/141 had always been opening) has no
high-speed capability at all; id 2 declares high-speed ranges reaching
480fps - matching NanoReview's number almost exactly. The Settings
screen's "not supported" reading of the *first* back camera was
correct for *that specific ID*, but wrong as an answer about the phone.
Fixed by scoring every back-facing ID and picking the best one instead
of the first:

```kotlin
val best = candidates.maxByOrNull { maxNormalFpsOf(it) }
```

This is a genuine, unconditional improvement kept regardless of what
follows below - on a phone where different lenses really do differ in
their normal-range ceiling, picking blindly-first could pick the worse
one.

## Second finding: this app's video pipeline can't use high-speed anyway

With the right camera now selected, requesting 60fps correctly chose
Camera2's constrained-high-speed session (`CameraConstrainedHighSpeed-
CaptureSession`, needed since no *normal* range on this phone reaches
above 30) - and that session immediately failed:

```
CameraSession: could not create capture session
  at android.hardware.camera2.utils.SurfaceUtils.checkHighSpeedSurfaceFormat
  at android.hardware.camera2.utils.SurfaceUtils.checkConstrainedHighSpeedSurfaces
  at android.hardware.camera2.impl.CameraDeviceImpl.createCaptureSessionInternal
  at android.hardware.camera2.impl.CameraDeviceImpl.createConstrainedHighSpeedCaptureSession
```

This is not device-specific - it's Android's own platform code refusing
the request. `CameraConstrainedHighSpeedCaptureSession` only accepts an
opaque `PRIVATE`-format output surface (a `SurfaceTexture`, or a
`MediaCodec`/`MediaRecorder` input surface). This app's `ImageReader` is
`YUV_420_888`, because [saveVideoSnippet](CameraSession.kt) needs raw
pixel access to hand each frame to `YuvImage.compressToJpeg` for the
frame-sequence player spec 140 built. Spec 141 assumed (wrongly, per its
own "Not verified" note) that a YUV `ImageReader` would work with a
high-speed session; it doesn't, on this Android version, full stop. So
even now that the *right* camera is being asked, this app's specific
capture technique genuinely cannot reach 60fps or above - not a
detection bug, an architecture limitation.

Silently attempting it was actively worse than the old always-wrong-
camera bug: selecting 60fps would fail camera setup, log a warning, and
silently fall back to **audio-only** (0 video frames) - a user who
picked "60 fps" expecting better video would get none at all, having
previously gotten reliable 30fps video by default. Fixed by removing
the high-speed path from `CameraSession.open` entirely rather than
leaving it there to keep failing: it now always uses a normal capture
session, and `FpsOptions.bestRange`/`supportedFps` are only ever called
with an empty high-speed list. `FpsOptions.needsHighSpeedSession` and
the high-speed half of `bestRange`/`supportedFps` stay in place (still
unit tested) as groundwork for a real fix later - see "Not covered".

Settings' disabled-option label changed from "(ei tuettu tällä
laitteella)" - not supported *on this device* - to "(ei vielä tuettu
sovelluksessa)" - not yet supported *in the app*. The old wording was
now actively misleading: the device supports 480fps just fine; this
app's implementation doesn't yet reach it.

## Verified on the phone

Reran the full investigation after the fix. Camera selection log now
reads `back camera candidates 0(30fps), 2(30fps), 3(30fps) -> picked 0`
- with the high-speed numbers no longer counted, all three tie, and
picking the first again lands on id 0, which happens to be the 50MP
main lens (the best one for this app's purposes anyway). Settings shows
30fps selected and enabled, 60/120/240 disabled with the corrected
wording. A full record → video-save cycle at 30fps went through cleanly
end to end again: `camera open: id=0 352x288 requestedFps=30 fps=4-30`,
59-61 frames written per shot, matching every previous verification
back to spec 140 - confirming this fix didn't regress the one frame
rate that does work.

## Not covered

Genuinely reaching 60/120/240/480fps on hardware that supports it (this
phone's camera id 2, confirmed capable) would need recording through
`MediaCodec`/`MediaRecorder` into an encoded video file with an opaque
surface, then decoding frames back out on demand for the stepping UI -
a materially bigger change than anything in specs 140-143, not
attempted here. This spec's job was narrower: stop the app from
claiming or attempting something it can't deliver, and fix the one part
of the original complaint (camera selection) that was a straightforward
bug.

Files: `android/app/src/main/java/tech/timolehtonen/shot/{CameraSession,
FpsOptions,MainActivity}.kt` (modified), `android/app/build.gradle.kts`
(version bump, 0.7.0/7 → 0.7.1/8). No production logic covered by
existing tests changed behaviour (`FpsOptions`'s high-speed-aware
functions are simply no longer called with real high-speed data, not
altered), so all 88 unit tests still pass unmodified; investigated and
verified end-to-end on a real OnePlus 10T 5G.
