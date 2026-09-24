# 143 — Android: autoplay, auto-advance through History, Dev mode filters the list

Four small changes to the live-listening app: the start button reads
"Aloita" instead of "Aloita kuuntelu"; opening a shot from History now
starts its audio/video/spectrogram playing immediately, no play tap
needed; a new switch in the detail dialog lets playback carry on
straight into the next shot in History when one finishes, turning the
list into something that can be browsed hands-free; and the Dev mode
switch now also filters what History itself shows, not just Statistics.

## "Aloita"

One string. `Button` still reads "Lopeta kuuntelu" while listening -
only the idle-state label shortened.

## Autoplay on open, and a working completion callback

`ShotDetailDialog`'s `MediaPlayer` is now started as soon as it's
prepared, in the same `LaunchedEffect(record)` spec 142 already used to
prepare it up front:

```kotlin
mediaPlayer = player
if (player != null) {
    player.start()
    playing = true
}
```

Getting here needed a real fix, not just an added `start()` call.
Spec 142's version built the `MediaPlayer` inside
`withContext(Dispatchers.IO)`, to keep the blocking `prepare()` call off
the main thread. That quietly broke `setOnCompletionListener`:
`MediaPlayer` delivers that callback on the thread that constructed the
player, which needs a `Looper` to receive it - `Dispatchers.IO`'s worker
threads don't have one. `prepare()` itself still succeeded, so nothing
about spec 142 looked broken in testing (play/pause/step were all
verified working, and none of that path touches completion) - but the
listener set in that block was never actually going to fire, silently.
This spec's auto-advance depends entirely on that callback, so it
surfaced the problem immediately. Fixed by constructing and preparing
the player inline instead of switching dispatchers - the file is a small
local WAV (a couple hundred KB), so `prepare()` blocking the composition
briefly is the same cost the working spec 139 version originally paid
by calling it straight from a button's `onClick`.

## Auto-advance to the next shot

A `Switch` + "Jatka automaattisesti seuraavaan" in the dialog, state
owned by `ShotScreen` (`var autoAdvance`) rather than by the dialog
itself - it has to survive the dialog moving from one `record` to the
next, which a `remember(record)`-scoped variable wouldn't.
`ShotScreen` also computes which shot is "next":

```kotlin
fun nextInHistory(current: ShotRecord): ShotRecord? {
    val ordered = visibleHistory.asReversed()
    val idx = ordered.indexOf(current)
    return if (idx in 0 until ordered.size - 1) ordered[idx + 1] else null
}
```

"Next" is whatever's next in the same newest-first order History
displays - the item visually below the current one, not chronologically
next. On completion, if the switch is on and there is a next shot, the
dialog just gets handed a new `record` (via an `onAdvance` callback that
sets `selectedShot`), and everything above (autoplay-on-open, the
prepare-and-start effect) takes it from there - auto-advance needed no
playback logic of its own, only "pick the next record and let opening it
work the way opening always works."

One correctness wrinkle: the completion listener is created once per
`record` inside a `LaunchedEffect`, but `autoAdvance` and `nextRecord`
can change without `record` changing (toggling the switch mid-playback,
or `nextRecord` simply being a different value read fresh on each
completion). A plain captured parameter would freeze at whatever it was
when the listener was set up. Fixed with `rememberUpdatedState`:

```kotlin
val latestAutoAdvance = rememberUpdatedState(autoAdvance)
val latestNextRecord = rememberUpdatedState(nextRecord)
val latestOnAdvance = rememberUpdatedState(onAdvance)
...
player?.setOnCompletionListener {
    ...
    val next = latestNextRecord.value
    if (latestAutoAdvance.value && next != null) latestOnAdvance.value(next)
}
```

## Dev mode filters History, not just Statistics

Previously Dev mode only decided whether *new* shots got persisted and
excluded from Stats (spec 139) - every already-persisted shot, dev-tagged
or not, always showed in History regardless of the switch. Now:

```kotlin
val visibleHistory = remember(history, devMode) { if (devMode) history else history.filter { !it.isDev } }
```

used everywhere History is read for display (`LazyColumn`'s
`visibleHistory.asReversed()`, the empty-state check, and
`nextInHistory`'s traversal order) - Statistics is unaffected, since it
already filtered dev shots out unconditionally on its own. With Dev mode
on, the list shows everything, same as before this spec. With it off,
every `isDev == true` shot - including the 47 migrated by spec 141 -
drops out of the visible list entirely, not just out of the numbers.

## Verified on the phone

A real OnePlus 10T 5G: the start button reads "Aloita". Turning Dev mode
off with only dev-tagged shots on disk (the spec 141 migration) emptied
History to "Ei vielä laukauksia."; turning it back on restored all of
them. Opening the 87 km/h shot from spec 140/141's testing started
playing immediately with no tap - frame counter and spectrogram line
both moving on open. Turned the new switch on, let that clip finish, and
the dialog moved itself straight to the next shot in the list (49 km/h,
13:32:54, an audio-only shot with no video) and that one started playing
on its own too, confirming autoplay and auto-advance compose correctly
and that the audio-only branch autoplays the same way the video branch
does.

## Not covered

Auto-advance always goes toward older shots (down the newest-first
list) - there's no "auto-advance backwards" or an explicit "seuraava"
button for browsing without waiting for a clip to finish. The switch's
state isn't persisted across dialog closes/reopens (still session-only,
matching `devMode`/`targetFps`'s own in-memory-only lifetime before this
spec). No indication in the dialog of how many shots remain before
auto-advance runs out (it simply stays on the last shot silently, same
as any control's "no next" case).

Files: `android/app/src/main/java/tech/timolehtonen/shot/MainActivity.kt`
(modified), `android/app/build.gradle.kts` (version bump, 0.6.0/6 →
0.7.0/7). No production logic outside Compose UI wiring changed, so all
88 unit tests still pass unmodified; installed and exercised end-to-end
on a real OnePlus 10T 5G.
