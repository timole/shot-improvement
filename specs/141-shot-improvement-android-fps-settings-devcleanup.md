# 141 — Android: fps Settings, dev mode by default, dev-tagging past test data

Three related changes: video's frame rate is now a Settings-screen choice
(30/60/120/240, offering only what the device's camera can actually
reach) instead of always "whatever the camera's ordinary API tops out
at"; Dev mode now defaults on rather than off; and the 47 shots already
sitting in `shots.jsonl` from building and testing spec 138-140 were
retroactively marked as dev shots, so they stop counting in Statistics
without being deleted.

## Persisting `isDev`

Spec 139 deliberately never wrote `isDev` to `shots.jsonl`: a dev shot
was never appended in the first place, so there was nothing to persist.
That's still true for *new* dev shots - `persist()`'s
`if (!recordingDev) ShotHistory.append(...)` guard is unchanged, and a
session recorded with Dev mode on still doesn't outlive the app process.
But marking *already-persisted* shots as dev retroactively (below) needs
the field to round-trip, so `ShotHistory` now writes and reads it like
any other field:

```kotlin
"\"hitT\":$hit,\"speedKmh\":$speed,\"snippetFile\":$snippet,\"isDev\":${record.isDev}}"
...
isDev = fields["isDev"] == "true",
```

A line written before this field existed simply has no match for
`"isDev"` and reads back `false` - the same "missing field defaults
sensibly" pattern `snippetFile` already used in spec 139.

## Marking the existing test data as dev

Building and testing specs 138-140 left 47 real entries in the phone's
`shots.jsonl` - hand claps, `triple_click.wav` played from a laptop, and
a handful of genuine ice-adjacent tests, all mixed in under real place
labels. None of it belongs in Statistics, but per this app's own
"nothing is ever deleted" convention (spec 138) and the standing
instruction to never delete recordings, it also shouldn't be erased.

Migrated in place: pulled `shots.jsonl` off the phone, appended
`,"isDev":true` to all 47 lines (a plain text transform - the schema is
flat enough not to need a JSON library for this one-off), and pushed it
back. Verified on the phone: every history row now shows the "DEV" tag,
and Tilastot shows "Ei vielä tilastoja." (no statistics yet) - the data
is still there (still in the file, still playable/viewable from History)
but no longer feeds Stats. No app code performs this migration - it's a
one-time data fix, not a repeatable feature.

## Dev mode on by default

```kotlin
var devMode by remember { mutableStateOf(true) }
```

The reasoning is the mirror image of why dev shots avoid Stats in the
first place: a session someone forgot to flag shouldn't quietly become
"real" data. Defaulting on means the opposite mistake - forgetting to
turn it *off* before a real session - is the one left possible, which is
the safer direction. Turning it off is one tap, same as turning it on
was before this spec.

## Video fps as a Settings choice

`FpsOptions` (new, pure logic, unit tested) is the device-independent
half: `CANDIDATES = [30, 60, 120, 240]`, and three functions that take
plain `FpsRange(lower, upper)` values (not `android.util.Range`, so this
stays testable without Robolectric) rather than reading a camera:

- `supportedFps(normalRanges, highSpeedRanges)` - which candidates this
  camera can reach at all, an "at least N fps" check: a range qualifies
  if its own upper bound is `>= target`, so a fixed 120fps high-speed
  range also satisfies a 60fps ask (the camera will simply run faster
  than requested, not short of it).
- `bestRange(target, normalRanges, highSpeedRanges)` - the tightest
  range that reaches `target`, preferring an ordinary range (cheaper,
  no high-speed session needed) over a high-speed one, and falling back
  to the highest available range of either kind if nothing reaches it.
- `needsHighSpeedSession(...)` - whether reaching `target` requires
  Camera2's separate constrained-high-speed path at all.
- `targetSizePxFor(fps)` - shrinks the requested output size as fps
  rises (480px up to 60fps, 320px up to 120, 240px above that), because
  the frame count in `LiveVideo`'s ~3s rolling buffer scales with fps:
  240fps × 3s × 480px-wide NV21 frames would be well over 100MB of raw
  buffered frames, where the same window at 240px stays in the tens of
  MB.

`CameraSession.open` now takes a `targetFps` parameter and, per
`FpsOptions`, either sets `CONTROL_AE_TARGET_FPS_RANGE` on an ordinary
`createCaptureSession` (as before spec 141, for 30/60fps on hardware
that supports them normally) or - new this spec - opens a
`CameraConstrainedHighSpeedCaptureSession` and drives it with
`createHighSpeedRequestList`/`setRepeatingBurst` instead of a single
repeating request, which is what most phones need to actually reach
120/240fps. `CameraSession.supportedFps(context)` exposes
`FpsOptions.supportedFps` to the UI without opening the camera device at
all (just reads `CameraCharacteristics`), so Settings can show real
disabled/enabled state cheaply.

The Settings screen (`SettingsScreen`, reached via a new "Asetukset" menu
item, positioned above "Versio" as asked) lists all four candidates as
radio buttons, greying out and labelling ("ei tuettu tällä laitteella")
whichever ones `supportedFps` says this device can't reach. Choosing one
persists it via a new `Prefs` object (`SharedPreferences`, one int key)
and it's read into `startListening()` the same way `distanceM` and
`devMode` already are - locked in for the session, takes effect from the
next "Aloita kuuntelu".

Because the stored/default preference (60) may itself not be reachable
on a given phone, `ShotScreen` corrects it once at startup: it queries
`CameraSession.supportedFps` off the main thread and, if the current
value isn't in that list, drops to the highest supported value at or
below it (or the lowest supported value if none qualify), persisting the
correction. Without this, Settings would show a disabled option as
"selected" - confusing, and not what the app would actually record at.

**Video encoding cost now scales with fps, so `saveVideoSnippet` moved
off the listening loop's own thread.** At 30fps a ~2s clip is ~60 JPEGs,
fast enough to encode inline (as spec 140 did). At 240fps it's roughly
480 - encoding that many synchronously on the same thread that also
calls `mic.read()` in a loop risked delaying the next read enough to
drop audio for a shot immediately following. `saveVideoSnippetAsync`
fires it on its own short-lived thread instead; the shot detail dialog
already handles "no video yet" gracefully (it's the same code path as
"no camera this session"), so a dialog opened before encoding finishes
just shows nothing until the next redraw.

Also fixed in passing: `saveSnippet`'s per-shot JSON sidecar had
`"appVersion": "0.3.0"` hardcoded, already two releases stale by this
spec. Now reads it live from `PackageManager.getPackageInfo`, the same
call `VersionDialog` already used.

## Verified on the phone

A real OnePlus 10T 5G: Settings shows all four fps options with only 30
enabled ("ei tuettu tällä laitteella" on 60/120/240) - this phone's back
camera characteristics report no `CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES`
entry above 30 and no `CONSTRAINED_HIGH_SPEED_VIDEO` capability, matching
spec 140's own "fps=4-30" finding. The stored default (60) auto-corrected
to 30 on first launch, with the radio selection landing on the enabled
option rather than a disabled one. A full record → video-save → playback
cycle at the (auto-corrected) 30fps target went through the new
fps-parameterised `CameraSession.open` path end to end: 59-60 frames
written per shot (matching the fixed 2-second window at 30fps), video
played back correctly in the detail dialog. Dev mode confirmed on by
default at launch; two shots recorded that way showed the "DEV" tag and
did not appear in `shots.jsonl` afterward. The 47-line migration was
confirmed both ways: every migrated row now shows "DEV" in History, and
Tilastot shows "Ei vielä tilastoja." where it previously showed real
numbers.

**Not verified**: actually reaching 120 or 240fps end-to-end, since no
camera available for testing offers a high-speed range - the
constrained-high-speed session code path (`createConstrainedHighSpeed-
CaptureSession`, `createHighSpeedRequestList`, `setRepeatingBurst`) is
new this spec and untested on real hardware. If a future phone's camera
does support it, `supportedFps` will offer 120/240, but that specific
capture path should get an on-device check before relying on it.

## Not covered

No UI affordance to un-mark a shot as dev (the migration was a one-time
manual fix, not a feature); a device with no camera at all still shows
Settings with every option disabled and an explanatory line, but there's
no way to know a *specific* value is wrong without trying it (no
sample-frame preview in Settings). `needsHighSpeedSession`'s "any range
whose upper covers the target" logic means selecting, say, 60fps on a
device whose only high-speed range is 240fps will actually record at
240fps, not something in between - correct in the "at least" sense the
Settings copy uses, but worth knowing if someone expects an exact rate.

Files: `android/app/src/main/java/tech/timolehtonen/shot/{MainActivity,
ShotHistory,CameraSession}.kt` (modified), `android/app/src/main/java/
tech/timolehtonen/shot/{FpsOptions,Prefs}.kt` (new),
`android/app/src/test/java/tech/timolehtonen/shot/{FpsOptionsTest,
ShotHistoryTest}.kt` (new/modified), `android/app/build.gradle.kts`
(version bump, 0.4.0/4 → 0.5.0/5). The phone's `shots.jsonl` migrated in
place (47 lines, all now `isDev:true`) - a data fix, not a code change.
88 unit tests passed; installed and exercised end-to-end on a real
OnePlus 10T 5G.
