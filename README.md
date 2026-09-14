# shot-improvement

Native Python (no browser) that records clips from this laptop's own
webcam and microphone, saved into `recordings/` (gitignored). Marks
each visible hand's palm with a green box using on-device MediaPipe
`PoseLandmarker` — a first step toward recognizing padel-racket and
hockey-stick motion. See `specs/` for the full history:
[080](specs/080-shot-improvement-native-python.md) (CLI),
[081](specs/081-shot-improvement-native-gui.md) (GUI),
[082](specs/082-shot-improvement-gui-controls-and-playback.md)
(camera/mic/speaker dropdowns, duration control, native in-window
playback, an annotation bug fix),
[083](specs/083-shot-improvement-logging-and-freeze-fix.md) (logging, a
real freeze fix, a measured throughput win),
[084](specs/084-shot-improvement-playback-controls.md) (full playback
transport controls),
[085](specs/085-shot-improvement-spectrogram-and-background-removal.md)
(spectrogram panel, background removal),
[086](specs/086-shot-improvement-performance.md) (deferred pose
inference, a measured fps win), and
[087](specs/087-shot-improvement-web-gallery.md) (cloud sync to a web
gallery).

(Two earlier browser-based versions, specs 078 and 079, were built
first and then replaced entirely — the original laptop this was built
on had ~3.9GB RAM total, and a persistent Chrome tab running
MediaPipe's WASM build was too much memory pressure. A native process
runs only for the duration of one recording and releases everything
afterward.)

**This repo used to be `experiments/shot-improvement/` inside the
`ai-timolehtonen-tech` monorepo** (specs 078–087 were written there;
their numbering is kept as-is here rather than renumbered from 001, to
avoid rewriting every in-file cross-reference). It was split out into
its own repo once it stopped being a small spike and started being a
standalone app worth versioning on its own. See "Web gallery" below for
what that move changed.

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Run

GUI (live annotated preview, record button, recordings list with
play/delete):

```
venv\Scripts\python gui.py
```

CLI (one clip, then exits):

```
venv\Scripts\python record.py [seconds]   # default 3
```

Both print/show the camera/mic actually selected, the achieved frame
rate, and save `shot-improvement-<timestamp>.mp4` +
`...-annotated.mp4` into `recordings/`. Both also log to
`logs/shot-improvement.log` (gitignored, size-capped) - set
`SHOT_IMPROVEMENT_DEBUG=1` for more detail when chasing something.

## Tests

```
venv\Scripts\pytest tests\
```

## Web gallery (spec 087) — needs new hosting

`web/{index.html,app.js}` (a Google Sign-In gated gallery) and
`core/cloud_sync.py` (uploads/deletes annotated clips to/from a private
GCS bucket, `wide-exchanger-463707-c6-shot-improvement`) were built
against a FastAPI server (`ai-timolehtonen-tech`'s `server/main.py`)
that served the page at `ai.timolehtonen.tech/shot-improvement` and
proxied the video bytes. **That server-side code was removed** from
`ai-timolehtonen-tech` as part of moving this repo out on its own — the
gallery page and cloud sync code are still here, but nothing currently
serves `web/` or lets a browser reach the bucket. `core/cloud_sync.py`
itself still works standalone (it talks to GCS directly, not through
that old server) — recording still auto-uploads/deletes given working
`gcloud auth application-default login` credentials — but there is no
web frontend serving the result until this repo gets its own hosting
(a small FastAPI/Flask app + a new Cloud Run service, or similar).

Status (bucket, OAuth client ID, session-secret) — set up during spec
087, still live in the `wide-exchanger-463707-c6` GCP project:

- GCS bucket `wide-exchanger-463707-c6-shot-improvement`
  (europe-north1, private, uniform bucket-level access).
- OAuth 2.0 Client ID (shared with an unrelated `ai-timolehtonen-tech`
  feature, Valkoapila) and a `SHOT_IMPROVEMENT_SESSION_SECRET` in
  Secret Manager — both still exist but nothing here reads them yet;
  a new server would need its own copy of the routes `server/main.py`
  used to have (see spec 087 for the exact shape) and its own
  deployment.

## Status

Both the CLI and the GUI have been exercised against a real camera/mic,
producing clips with video+audio and, when a hand was actually in
frame, real detected palm boxes throughout the clip (a bug where boxes
could vanish after the first frame or two, and a separate real ~12s
camera freeze after recording, were both found and fixed - see specs
082/083). Default recording duration is 10s. Camera/mic/speaker
selection, the live camera switch, a full record cycle, and full
playback transport controls (play/pause/stop, frame-step, scrub,
speed, volume, loop) have all been exercised through real GUI clicks.
The actual padel/hockey-stick tracking feature this whole app is a
spike for is still unspecced.
