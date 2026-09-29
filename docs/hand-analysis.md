# Hand markers and fast playback (spec 162)

`tools/process_shots.py` processes explicit stems first, then `--latest 30`
for a bounded backfill. It never replaces raw camera video, audio or phone
metadata. Run on the desktop with its existing MediaPipe/OpenCV environment.

```
venv/Scripts/python.exe tools/process_shots.py <stem> --height-cm <height> --top-hand right
venv/Scripts/python.exe tools/process_shots.py --latest 30 --height-cm <height> --top-hand right
```

The source is resampled using its presentation timestamps to 240 fps before
annotation. Yellow squares use visible wrist/finger pose landmarks; hidden
hands are omitted. Tracks and review frames remain in `recordings/spec162`.

Speed is the fitted camera-plane palm velocity in the last 50 ms before
the audio metadata's shot timestamp. Scale is shooter height divided by
median visible articulated body length. This is an approximation, not a
calibrated 3D measurement: perspective, occlusion, pose errors and residual
audio/video offsets affect it. Missing calibration, missing contact-window
landmarks or large tracking residuals yield no number. The UI labels valid
numbers as estimates and gives the height used. Do not substitute the other
hand when the selected top hand is hidden.

`--calibrate-only` updates estimates from saved tracks without re-encoding.
Use it to correct the height or top-hand selection after marker processing.

Versioned MP4/JPEG artifacts live at `android/processed/`. The gallery
manifest `android/index.json` holds rows and precomputed playback metadata.
Publication uploads both artifacts before committing their version to the
manifest. ETag-conditional merge/retry prevents concurrent uploads and
analysis publishers from losing one another's rows. Servers cache this
manifest for 15 seconds. Existing nonprocessed shots retain the fallback
encoder; a per-stem lock prevents video and metadata requests encoding the
same shot simultaneously. Private authenticated routes are retained.

After changing JSX, run `node tools/build_web.cjs` and commit the generated
`web/app.bundle.js`. The page uses precompiled JavaScript, with no Babel
download or compilation in the browser.

## September 2026 backfill

Processed exactly the latest 30 videos (2026-09-27 12:47:09 through
12:51:15), starting with the six requested 12:50 examples. Calibration is
178 cm, right hand at the top of the stick. The six example clips have no
reliable right-hand track during the contact window; their speed stays
unavailable. Only one of the 30 passes the current confidence gates. This
is a visibility limitation, not permission to substitute left-hand speed.
A camera angle with the right wrist/palm visible through contact is needed
for dependable top-hand estimates in these shots.

Live API baseline: gallery 4.6 s, first 64 KiB of the 88 km/h clip 10.7 s.
After deployment: gallery 0.04-0.23 s, initial video chunk 2.5 s on first
access and 0.03-0.09 s cached. These are HTTP timings from this workstation,
not browser time-to-playing or a guarantee for other networks/devices.
