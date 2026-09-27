# Android detector and A/V calibration — Spec 160

Bench-tested on the OnePlus CPH2415 at 240 fps on 2026-09-27.

## Findings

- `dev-shot-20260927100611-1.wav` contains one clear snap at 0.704 s,
  and no second impulse in the valid hit window. A visible spectrogram
  line alone does not establish a shot/hit pair. At 22.5 m the accepted
  interval is approximately 0.543–2.092 s, including sound return.
- WAVs were saved immediately on hit confirmation. They therefore ended
  well before the requested post-shot tail. All six new test clips have
  1 s before and 4 s after the shot (within one audio sample).
- Audio capture used to start before blocking camera initialization.
  The small AudioRecord buffer could overflow while nobody read it.
  A wall-clock timestamp taken after opening the mic did not identify
  sample zero, and recorder.start() did not identify the first camera frame.
- Playback prepared/started audio and video independently. Frame stepping
  also passed microseconds to MediaPlayer.seekTo, whose unit is milliseconds.
- The September 25 14:53:14 reference MP4 has 1187 samples, including a
  dense run of old keyframe preroll timestamps near zero. Its frame number
  cannot be converted to time by dividing by a nominal/average frame rate.

## Changes

Camera setup now precedes microphone capture, and the recorder is armed
before the repeating capture request. Sample zero is estimated from the
median of 16 AudioRecord hardware timestamps; the first camera exposure is
mapped to the same monotonic clock. Devices without comparable camera
timestamps use a callback-time estimate. This is a fallback, not a
guarantee of identical precision on every phone. No fixed 200 ms offset
is applied globally.

Playback waits for both players and both seek completions. Frame labels
and stepping use the MP4 presentation timestamp table, and MediaPlayer
receives milliseconds with SEEK_CLOSEST.

Only a return hit inside the physical time window gets a 3 dB lower
absolute floor. Adaptive background-noise rejection, local prominence,
and frequency filtering remain. Early echoes do not replace a pending
shot; rejected noise does not suppress a later hit; the hit timeout waits
for an already-started candidate to finish its lookahead.

Stopping finishes the pending pair and four-second tail without accepting
new shots. WAV saving waits for the tail. Dev mode keeps the latest raw
session as `dev-session.wav/.mp4/.json` for diagnosis; these stay local and
are removed by the existing dev-file cleanup on the next app startup.

## Bench results

`tools/calibrate_av.py` generates paired broadband clicks and black/white
flashes on the primary laptop display. It schedules flashes against the
speaker DAC clock. The phone faced that display; the user confirmed the
clicks and flashes were presented correctly.

Run with `--output-device 11 --gain 1.8` on this laptop. Device IDs vary;
inspect `sounddevice.query_devices()` elsewhere. Defaults generate six
pairs, with the interval for a 22.5 m, 100 km/h shot including sound return.

| Measurement | Result |
| --- | --- |
| New live detections | 6 of 6 pairs |
| Measured speed | 99.593 km/h for each pair; target 100 km/h |
| Audio clip duration | 5.000 s, within one sample |
| Post-shot audio | 4.000 s, within one sample |
| Flash/audio difference in trimmed clips | Audio 14–41 ms before detected screen transition |
| Quieter stimulus | Below normal shot floor; no false shots |

The remaining measured difference includes laptop display latency,
refresh quantization, acoustic propagation and capture uncertainty.
This bench test does not demonstrate single-frame (4.17 ms) absolute
accuracy. The first flash in an earlier run was a startup outlier; the
other 11 pairs were within 5–30 ms. The result does not justify shifting
every phone's recordings by a fixed residual correction.

A final one-pair run on the updated build measured 100.250 km/h. Stopping
during the tail still produced a 5.000 s WAV with 4.000 s after the shot.
Validation: 268 Python tests and 96 Android tests per build variant passed.

Evidence copies and measurements are local under `recordings/spec160/`
(gitignored). Existing user recordings are not rewritten.

API references: [AudioRecord timestamps](https://developer.android.com/reference/android/media/AudioRecord#getTimestamp(android.media.AudioTimestamp,int)),
[camera capture timestamps](https://developer.android.com/reference/android/hardware/camera2/CameraCaptureSession.CaptureCallback#onCaptureStarted(android.hardware.camera2.CameraCaptureSession,android.hardware.camera2.CaptureRequest,long,long)),
and [MediaPlayer seek units](https://developer.android.com/reference/android/media/MediaPlayer#seekTo(long,int)).
