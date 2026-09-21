# 136 — Android app: puck speed from sound, offline

A native Kotlin/Compose app in `android/` that measures one shot at a time
with the phone's microphone alone, fully offline. The player stands on the
blue line with the phone beside them and shoots into the end boards; the
app records 10 s of mono 16-bit audio, finds the stick-puck impact and the
board impact, and shows the puck's average speed in km/h ("Nopeus: 104
km/h", "Ero 0,875 s · 22,5 m · sinisestä viivasta päätyyn"). Video, the
stick view, history and the other shot positions are not in v1. Distance
is fixed at the `blue_line_to_end` 22.5 m of `core/claps.py`.

## Acoustics differ from the desktop rig

The desktop mic sits on the camera at the goal; the phone sits at the
shooter. There the board bang is heard *before* the flight ends; here it
is heard after the flight **plus** the sound's trip back:
`dt = d/v + d/c`, so `v = d / (dt - d/c)` with c = 337.4 m/s (about 10 C,
hard-coded: a 10 C error moves a 130 km/h reading 0.25 km/h) and
d/c = 66.7 ms. Leaving the term out would read 5.5 % low at 70 km/h,
7.6 % at 100 and 9.7 % at 130 (the error is `-v/(v+c)`, independent of
distance). The number is the average over the flight, a few percent under
the release speed (air drag), the same bias the desktop app has.

The pairing window is derived from a 40-170 km/h band instead of scaled
from the desktop's 1.5-4.0 s: 0.543-2.092 s of measured gap. Scaling the
desktop window would silently reject a 160 km/h shot.

The desktop code has the same acoustic error with the opposite sign
(`core/claps.py` `puck_speed_kmh` ignores the sound travel time, so the
default 18.5 m position reads roughly 6-7 % high). It is not touched here
because fixing it changes every historical number; it wants its own spec.

## Detector port (`android/.../Claps.kt`)

A function-for-function port of `core/claps.py` with the same constant
names, and these deliberate differences: the spectral gate uses a fixed
8192-sample Hann window (185.8 ms) with an iterative radix-2 FFT instead
of 0.15 s = 6615 samples, which is not a power of two and would need
zero-padding that shifts the band ratio; the noise floor is -45 dBFS
rather than 500 raw counts (which is one webcam's gain), plus a
prominence gate (peak RMS >= 6x the local median over +-1 s); the NMS
separation is 0.45 s (still merges a stick windup, still below the fastest
plausible 0.543 s gap). As in Python, a peak rejected by a gate still
suppresses its neighbours. Median uses numpy's even-count semantics.

## Capture (`ShotRecorder.kt`)

One continuous `AudioRecord` stream, so only sample offsets matter - no
wall clock and no A/V sync. Source order: UNPROCESSED (when the device
says it supports it), VOICE_RECOGNITION, CAMCORDER, MIC; device-native
sample rate with 44.1 kHz as fallback; AGC, noise suppression and echo
cancellation are switched off where the device offers them, and the
outcome is logged. A short read or read error marks the capture as an
overrun (samples are missing, so dt would be wrong) and no speed is
shown. No countdown beeps, unlike the desktop: a beep would be a loud
impulse in the analysed signal.

Every capture is kept: `shot-<yyyyMMddHHmmss>.wav` (PCM16, exactly the
`AudioRecord` bytes) and a `.json` sidecar (sample rate, audio source,
effect states, clipped fraction, event times, speed, device model, a null
`groundTruthKmh` to fill in) under the app's external `Music/recordings`
folder, reachable over USB without any permission. The app never deletes
them. `tools/analyze_wav.py` replays such a WAV through the desktop
detector and prints the corrected and uncorrected speed, for re-tuning
against real ice recordings.

## Installing

`/android` (a Finnish install page) and `/app.apk` are public routes in
`server/main.py`. The APK lives in the existing `clips` container as
`downloads/shot-improvement.apk` (uploaded by `tools/publish_apk.py`, the
same `az login` path as `core/azure_sync.py`) and is read fresh on every
request with `Cache-Control: no-store`, since it is overwritten per
release; a new APK needs no server redeploy. v1 is debug-signed, so the
phone asks to allow installs from Chrome and Play Protect may warn.

## Not proven yet

Detection thresholds (floor, prominence, spectral gate) come from the
desktop webcam's recordings and a synthetic signal; whether they hold on a
phone mic at a real rink is what the first on-ice WAVs are for.

Files: `android/` (new), `tools/analyze_wav.py`, `tools/publish_apk.py`,
`server/main.py`, `server/blob_videos.py`, `server/tests/test_routes.py`,
`web/android.html`.
