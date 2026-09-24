# 145 — Android: genuine 60/120/240fps video, via MediaRecorder + decode

Spec 144 found that reaching 60fps+ needs Camera2's constrained-high-
speed capture session, and that this app's raw-YUV-`ImageReader`
capture couldn't use it (the session type rejects that surface format
outright), so it backed off to always requesting a normal session -
correct as far as it went, but it meant 60/120/240fps stayed
unreachable. This spec rebuilds capture around `MediaRecorder` (whose
input surface *is* the opaque format the high-speed session needs) and
decodes each shot's window back out of the recording afterwards - the
"materially different pipeline" spec 144 flagged as the real fix.
**Verified on the phone at 120fps and at 240fps - both work end to end,
with per-frame stepping at genuine 1/120s and 1/240s granularity.**

## Recording the whole session, not a rolling buffer

`CameraSession` no longer grabs frames itself. It configures a
`MediaRecorder` (`VideoSource.SURFACE`, H.264, frame rate and bitrate
matched to the requested capture rate) and drives the camera - through
a normal or, now, a genuinely-working constrained-high-speed session,
whichever [FpsOptions.needsHighSpeedSession] says the target needs -
with the recorder's own surface as the sole target, for the *entire*
listening session, stopping only in `close()`. This replaces spec 140's
`LiveVideo` rolling buffer (removed) entirely: a `MediaRecorder`-written
MP4 isn't safely seekable until `stop()` finalises it, so there is
nothing to usefully snapshot mid-session anyway - a shot's own window is
decoded back out of the finished recording once listening stops.

```kotlin
val recorder = MediaRecorder().apply {
    setVideoSource(MediaRecorder.VideoSource.SURFACE)
    setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
    setVideoEncoder(MediaRecorder.VideoEncoder.H264)
    setVideoSize(size.width, size.height)
    setVideoFrameRate(bestRange.upper)   // not a lower rate - see below
    setVideoEncodingBitRate(bitRateFor(size.width, size.height, bestRange.upper))
    setOutputFile(outputFile.absolutePath)
}
```

The declared frame rate matches the actual capture rate, not a lower
one - a "slow motion" video (240fps captured, 30fps declared) plays back
8x slower instead of giving many distinct steppable frames, which is
the opposite of what this feature is for.

**Camera selection** is unchanged in spirit from spec 144 (score every
back-facing ID, pick the best), but now scores by combined normal *and*
high-speed capability again, since a `MediaRecorder` surface can
actually use both. On this app's own OnePlus 10T this correctly lands
back on camera id 2 (480fps-capable) rather than id 0 (30fps only).

## Clock offset between audio and video

The mic still opens first, so the recording's own internal timeline
(0 at `mediaRecorder.start()`) always starts a little after the audio
clock `shotT` is measured against. `CameraSession.offsetS` captures that
gap (`System.nanoTime()` at recorder-start minus a `sessionStartNanos`
snapshot taken right after `MicSession.open()`), and `extractVideoSnippet`
subtracts it before decoding:

```kotlin
val startS = (record.shotT - PREROLL_S).coerceAtLeast(0.0) - camera.offsetS
val endS = record.shotT + POSTROLL_S - camera.offsetS
```

## Decoding: MediaMetadataRetriever, not raw MediaCodec+ImageReader

The first implementation decoded with `MediaCodec` into a `YUV_420_888`
`ImageReader` Surface and converted to NV21 by hand - the same
conversion spec 140's live-capture path already used successfully. On
real hardware it crashed the app outright:

```
Abort message: 'JNI DETECTED ERROR IN APPLICATION: non-zero capacity for nullptr pointer: 1'
```

Camera-captured `Image`s support that pattern fine (specs 140-144's own
extensive testing proves it), but this phone's hardware video *decoder*
doesn't reliably hand back CPU-readable YUV planes the same way when
rendering to an `ImageReader` Surface - a real device/driver limitation
discovered the same way spec 144's finding was: by actually running it,
not by reasoning about the API in the abstract.

Fixed by switching to `MediaMetadataRetriever`, Android's high-level
frame-extraction API - same underlying decoder, but through a path
built and tested specifically for this "pull frames out of a video"
use case:

- **Fast path (API 28+, this phone included):** one
  `getFramesAtIndex(startIndex, numFrames)` call decodes a whole run of
  consecutive frames, reusing decoder state internally - much quicker
  than one seek-and-decode per frame for a high-fps window's worth.
  `startIndex`/`numFrames` come from the window's seconds times the
  session's own known capture fps (`camera.fps`), not from reading
  frame timing back out of the file.
- **Fallback (API 26/27, or if the fast path fails for any reason):**
  one `getFrameAtTime(..., OPTION_CLOSEST)` call per frame - slower, but
  a public API available since long before this app's minSdk.

Frame times in the sidecar are therefore `index / fps`, an assumed-
uniform spacing rather than each frame's own measured timestamp -
`MediaMetadataRetriever` doesn't hand that back. Reasonable given the
recording targets a fixed capture rate throughout.

## Everything downstream is unchanged

The on-disk shape a shot's video takes - `<stem>-frames/frame_NNNN.jpg`
plus `<stem>-frames.json` - is exactly what spec 140 defined, so
`ShotFiles.readFrames`, `VideoFrameBox`, and all of spec 142/143's
synced-playback and auto-advance logic in `ShotDetailDialog` needed no
changes at all. Only how the frames get onto disk changed.

## A real UX trade-off: video is no longer instant per-shot

Specs 140-144 saved a shot's video snippet within moments of the shot
happening. Because a `MediaRecorder` file isn't safely readable until
the whole recording stops, this spec can only extract a shot's frames
*after* "Lopeta kuuntelu" - all pending shots from the session are
decoded in one pass once the recording is finalised:

```kotlin
camera?.close() // finalises videoFile so it's safely seekable
if (camera != null) extractPendingVideoSnippets(camera, pendingVideoShots)
```

A shot's dialog opened while still listening (or before this pass
finishes) shows no video yet - not broken, just not there yet, the same
graceful "no video for this shot" path that already covers an old shot
or a camera-unavailable session. This is a deliberate trade-off for
reliably reaching genuine high fps at all; not revisited in this spec.

## Verified on the phone

A real OnePlus 10T 5G, both at 60fps (which resolves to the camera's
actual 120fps fixed high-speed range - no discrete 60 range exists) and
at 240fps:

- `camera open: id=2 640x480 requestedFps=60 fps=120-120 highSpeed=true` /
  `requestedFps=240 fps=240-240 highSpeed=true` - the constrained-high-
  speed session that always failed in spec 144's testing now configures
  successfully.
- A ~2s shot window decoded to exactly the expected frame count each
  time: 239-240 frames at 120fps, 480 frames at 240fps.
- Playback in `ShotDetailDialog` ran the full clip through to completion
  without a crash or stall, auto-resetting correctly at the end.
- A single frame-step moved the counter by exactly 1/120s (0.000s →
  0.008s) and, separately, 1/240s - real per-frame granularity, not an
  approximation - with the spectrogram's playhead line moving the
  matching tiny amount each time.
- Settings' fps radio buttons all show enabled (no "not supported"
  labels) with the camera-scoring fix in place.

## Not covered

Video is not available until the session's "Lopeta kuuntelu" (see
above) - a real UX change from specs 140-144, not solved here.
Extraction speed at very high fps/long windows wasn't benchmarked
precisely - `getFramesAtIndex` is materially faster than the naive
per-frame fallback, but no number is quoted here since it depends on
device and window size. "Stick bending visible" still isn't verified
with a real shot (only synthetic audio + a wall in front of the camera,
same limitation every prior video spec has flagged) - though 120-240fps
genuine capture is now in place to make that check meaningful whenever
it happens.

Files: `android/app/src/main/java/tech/timolehtonen/shot/{CameraSession,
MainActivity,ShotFiles,FpsOptions}.kt` (modified),
`android/app/src/main/java/tech/timolehtonen/shot/VideoDecoder.kt`
(new), `android/app/src/main/java/tech/timolehtonen/shot/LiveVideo.kt`
and `android/app/src/test/.../LiveVideoTest.kt` (removed - the rolling
buffer this spec no longer needs). `android/app/build.gradle.kts`
(version bump, 0.7.1/8 → 0.8.0/9). 82 unit tests passed (88 minus
`LiveVideoTest`'s 6, nothing else changed); installed and exercised
end-to-end on a real OnePlus 10T 5G at 60/120/240fps, including a crash
found and fixed by that on-device testing.
