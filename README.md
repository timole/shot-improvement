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
gallery),
[092](specs/092-shot-improvement-performance-profiling.md) (opt-in
time/memory/disk profiling, `tools/bench_record.py` /
`tools/bench_live.py`, and a fused annotate+spectrogram pass), and
[093](specs/093-shot-improvement-deferred-processing.md) ("Vain
nauhoitus" recording-only mode - capture now, process the backlog
later on request), and
[094](specs/094-shot-improvement-raw-sync-and-previews.md) (raw clips
now sync to the cloud too, plus a preview image per video), and
[095](specs/095-shot-improvement-azure-gallery.md) (a second,
Azure-hosted gallery at
[shot.timolehtonen.tech](https://shot.timolehtonen.tech) - `server/`
in this repo, backed by Azure Blob Storage), and
[096](specs/096-shot-improvement-microsoft-entra-auth.md) (that
gallery's sign-in swapped to Microsoft Entra ID - no GCP dependency
anywhere in that service), and
[097](specs/097-shot-improvement-av-sync-fix.md) (fixed an
audio/video sync bug - video frames were timestamped assuming even
capture spacing, which this hardware's camera doesn't actually
provide).

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

Check **"Vain nauhoitus (käsittele myöhemmin)"** (spec 093) before
recording to skip all post-capture processing (pose annotation,
spectrogram, ffmpeg encoding) - useful when the machine is too slow to
both process an older clip and capture a new one at the same time.
Raw frames are saved to `pending/` (gitignored) instead; click
**"Käsittele odottavat"** whenever convenient to turn everything
queued up into the usual `recordings/` mp4 pairs.

CLI (one clip, then exits):

```
venv\Scripts\python record.py [seconds]   # default 3
```

Both print/show the camera/mic actually selected, the achieved frame
rate, and save `shot-improvement-<timestamp>.mp4` +
`...-annotated.mp4` into `recordings/`. Both also log to
`logs/shot-improvement.log` (gitignored, size-capped) - set
`SHOT_IMPROVEMENT_DEBUG=1` for more detail when chasing something, or
`SHOT_IMPROVEMENT_PROFILE=1` for a per-stage time/memory/disk report
(spec 092) - printed to stdout and written to `logs/bench-<ts>.json`.

## Performance benchmarking (spec 092)

```
venv\Scripts\python tools\bench_record.py [seconds]   # default 3; headless, real camera/mic
venv\Scripts\python tools\bench_live.py [num_frames]  # default 200; live preview only, no recording
```

Both force profiling on and print a stage-by-stage table (wall time,
working-set delta, process I/O, temp-dir size) plus a per-frame
operation breakdown, and write the same data to
`logs/bench-<timestamp>.json` so two runs can be diffed.

## Tests

```
venv\Scripts\pytest tests\
```

## Two galleries (specs 087/094/095, both live)

`core/cloud_sync.py` dual-writes every clip (raw + annotated + a JPEG
preview per video, spec 094) to **two** cloud backends now (spec 095):
a private GCS bucket and Azure Blob Storage. Two independent, gated
gallery frontends read from them:

- **`https://ai.timolehtonen.tech/shot-improvement`** (spec 087) -
  GCP/Cloud Run, reads the GCS bucket. Server-side code lives in the
  `ai-timolehtonen-tech` repo (`server/main.py` +
  `server/shot_improvement_videos.py` + `server/valkoapila_auth.py`),
  **not** this one - **correction to earlier notes in this file**:
  that code was NOT actually removed when this repo split out ("Spec
  078: restore shot-improvement web gallery hosting" put it back, in
  that repo's own numbering) - it's live and in active use. It serves
  its own copy of the frontend from
  `experiments/shot-improvement/web/` in that repo - a **separate**
  copy from `web/` here, using a `/shot-improvement`-prefixed path
  scheme, since it shares that server with other, unrelated features.
  A change there needs a `gcloud builds submit --config
  deploy/gcp/cloudbuild.yaml` (from that repo's root) to reach the
  live site.
- **`https://shot.timolehtonen.tech`** (spec 095) - Azure Container
  Apps, reads Azure Blob Storage. Server-side code is `server/` **in
  this repo**, serving `web/{index.html,app.js}` **in this repo**
  directly (top-level `/api/...` paths, no prefix - nothing else
  shares this domain). Redeploy with `az acr build --registry
  shotimprovementacr --image shot-improvement-server:<tag> --file
  server/Dockerfile --platform linux/amd64 .` then `az containerapp
  update -n shot-improvement-server -g shot-improvement --image
  shotimprovementacr.azurecr.io/shot-improvement-server:<tag>`.

**`web/` in this repo is the Azure gallery's frontend, not the GCP
one** - the two frontends are deliberately separate copies with
different path schemes now, not meant to be kept identical.

### GCP gallery status

Set up during spec 087, still live in the `wide-exchanger-463707-c6`
GCP project:

- GCS bucket `wide-exchanger-463707-c6-shot-improvement`
  (europe-north1, private, uniform bucket-level access).
- OAuth 2.0 Client ID (shared with an unrelated `ai-timolehtonen-tech`
  feature, Valkoapila) and a `SHOT_IMPROVEMENT_SESSION_SECRET` in
  Secret Manager, both read by the live server.

### Azure gallery status

Set up during specs 095/096, live in Azure subscription "Azure
subscription 1" (Pay-As-You-Go), resource group `shot-improvement`,
Sweden Central:

- Storage account `shotimprovement`, container `clips` (Standard_LRS,
  Hot, no public blob access). Laptop writes via `az login` +
  `DefaultAzureCredential` (`Storage Blob Data Contributor` on the
  signed-in account); the server reads via its Container App's
  system-assigned managed identity (`Storage Blob Data Reader`) - no
  stored secret either way.
- Container Registry `shotimprovementacr`, Container Apps environment
  `shot-improvement-env` + app `shot-improvement-server`.
- Custom domain `shot.timolehtonen.tech` bound with a managed
  (DigiCert) certificate - DNS at Vercel (`vercel dns add`): the
  domain's existing CAA records needed a `0 issue "digicert.com"`
  entry added alongside the GCP-side `pki.goog` one, or issuance
  fails.
- **Sign-in is Microsoft Entra ID, not Google** (spec 096 - "let's not
  use GCP at all, only Azure") - App Registration `shot-improvement`
  (`AzureADandPersonalMicrosoftAccount`, so the owner's personal
  Microsoft account works, not just an org Entra account), client
  ID/secret live in the Container App's `microsoft-client-id`/
  `microsoft-client-secret` secrets. Rotate the secret with `az ad app
  credential reset --id c3927ca8-5149-42b8-9477-e7832c3aea46` then
  `az containerapp secret set ...` + restart the revision (secret
  changes don't auto-restart).
- `--min-replicas 1` currently (not scaled to zero) - deliberate while
  the certificate was first issued; Azure's own docs say the app must
  stay running through both initial issuance and every ~45-day
  renewal. Dropping to 0 trades a small cost saving for a cold start
  on the first request after idling and re-exposes that renewal risk.

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
