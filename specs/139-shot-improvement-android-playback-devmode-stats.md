# 139 — Android: shot playback, a dev mode, and statistics

Three additions to the live-listening app (spec 138): tapping a history
row can now play the shot's own audio back, not just look at its
spectrogram; a "Dev mode" switch keeps test shots out of both the
persisted history and the statistics; and a hamburger menu adds a
statistics screen (median/min/max speed per day/week/month, to see
training progress) and a version screen.

## Fixed-length snippet, and playback

Every shot's saved WAV is now a fixed two seconds - one second before the
shot to one second after - instead of shot-to-hit. `saveSnippet` no
longer reads `record.hitT` or `Geometry.hitDelayWindow` at all:

```kotlin
val startS = (record.shotT - PREROLL_S).coerceAtLeast(0.0)
val endS = record.shotT + POSTROLL_S
```

This is deliberately shot-centred, not shot-to-hit: an unpaired shot (no
audible hit) gets exactly the same two seconds as a paired one, and nei-
ther the WAV nor the static spectrogram is guaranteed to contain the hit
sound any more - for a shot whose hit lands more than a second later
(common past a few metres), the hit marker simply falls outside the clip
and is not drawn, the same "outside the window" path the live view's
markers already used. `ShotDetailDialog` gained a "▶ Toista ääni" button:
one `MediaPlayer` per dialog instance, `setDataSource` on the snippet
file's path directly (a plain WAV on local storage needs nothing else),
released via a `DisposableEffect` keyed on the shown record so switching
shots or closing the dialog always stops any playback in progress rather
than leaking a player. Tested on the phone (`adb logcat` showing
`MediaPlayer start called` with no errors) and confirmed visually that
the button toggles to "⏸ Pysäytä" while playing.

## Dev mode

A `Switch` top-right, disabled while listening (locked in for a session
the same way the rink map is): when on when "Aloita kuuntelu" is
pressed, every shot from that session is a **dev shot**. A dev shot:

- still gets its own row in the current session's on-screen history
  (with a red "DEV" tag) and its own snippet WAV/JSON, so it can be
  reviewed and played back like any other shot while the app is running;
- is never appended to `shots.jsonl` - `persist()` in the listening loop
  skips `ShotHistory.append` entirely when the session is a dev one;
- is excluded from Statistics (`StatisticsScreen` filters `!it.isDev`);
- and does not outlive the app process: its snippet files are named
  `dev-*` instead of `shot-*`, and `ShotFiles.cleanupDevFiles`, called
  once from `onCreate`, deletes any `dev-*` files left over from a
  previous run before the screen is even shown.

`ShotRecord.isDev` exists purely for this in-memory bookkeeping - it is
never written to or read from the JSONL schema (a dev shot is simply
never handed to `ShotHistory.append` in the first place), so the on-disk
format is unchanged and every existing `ShotHistory`/`ShotRecord` test
still passes untouched.

Verified on the phone: with Dev mode on, a detected shot showed the DEV
tag and its own `dev-shot-*.wav`/`.json` appeared in the recordings
folder; `shots.jsonl`'s last line was unchanged (not written); after an
`am force-stop` + relaunch, the `dev-*` files were gone and the shot no
longer appeared anywhere (never having been persisted).

## Hamburger menu, Statistics, Version

A `☰` `TextButton` top-left opens a two-item `DropdownMenu` (no
`material-icons` dependency added just for a menu glyph - a Unicode
character serves as well and keeps the same minimal-dependency style as
the rest of this app). "Tilastot" and "Versio" swap in place of a
navigation library, via a small `Screen` sealed interface the main
`Column` branches on - there are only two destinations, so a full nav
graph would be more machinery than the app needs.

`Stats.kt` is pure logic (`java.time`, available since API 26, no
desugaring needed): `aggregate(records, period)` groups shots by
calendar day, ISO week, or calendar month and returns each group's
count, min, median, average and max speed, oldest period first - unpaired
shots (no `speedKmh`) do not contribute a row, the same as they never
contributed to any other speed number in the app. `StatisticsScreen`
shows Päivä/Viikko/Kuukausi tabs and the resulting rows newest-first,
matching the History list's own convention; the intent (seeing whether
speed rises as the user trains) is served by scrolling through the list
rather than a chart - Compose has no built-in charting and one more
dependency for a handful of numbers per period was not worth adding.

The version dialog reads `PackageManager.getPackageInfo` directly
(`longVersionCode` from API 28, the deprecated `versionCode` int below
it - not `PackageInfoCompat`, to avoid a `androidx.core` version-surface
dependency for one call).

Verified on the phone: the menu opened both items; Tilastot showed real
aggregated numbers (count/min/median/avg/max) from the day's actual test
shots, correctly excluding a dev-mode shot from the count; Viikko showed
the same numbers under an ISO week label; Versio showed "Versio 0.3.0
(3)", matching `build.gradle.kts` (bumped from 0.2.0/2 this spec).

## Not covered

No way to delete a shot or a dev session from the UI (only the automatic
dev-file cleanup at startup); Statistics has no chart, only numbers; a
period with very few shots gives a technically-correct but not very
meaningful median (no minimum-sample-size floor).

Files: `android/app/src/main/java/tech/timolehtonen/shot/{MainActivity,
ShotHistory,ShotFiles}.kt` (modified), `android/app/src/main/java/tech/
timolehtonen/shot/Stats.kt` (new), `android/app/src/test/java/tech/
timolehtonen/shot/StatsTest.kt` (new), `android/app/build.gradle.kts`
(version bump). 67 unit tests passed; installed and exercised on a real
OnePlus 10T 5G (playback, dev mode's full record → restart → gone cycle,
both menu screens).
