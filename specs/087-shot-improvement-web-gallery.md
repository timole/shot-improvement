# 087 — Shot-improvement web gallery, synced from the laptop

## What

A new page, `ai.timolehtonen.tech/shot-improvement`, listing and
playing the annotated training clips recorded by the native laptop app
(`experiments/shot-improvement/`, specs 080–086). Gated by Google
Sign-In, allowlisted to the owner alone (`timo.lehtonen@gmail.com`) —
same server-verified-identity pattern as spec 074's Valkoapila
dashboard, reusing `server/valkoapila_auth.py`'s functions (generic
despite the module name) under this feature's own cookie name
(`shot_improvement_session`), config values
(`SHOT_IMPROVEMENT_GOOGLE_CLIENT_ID`/`_ALLOWED_EMAILS`/`_SESSION_
SECRET`), and OAuth Client ID (the same one already created for
Valkoapila — one GCP project, one web origin, so a second client would
buy nothing).

The clips live in a new private GCS bucket
(`wide-exchanger-463707-c6-shot-improvement`, uniform bucket-level
access, public access prevention enforced) — only the **annotated**
video of each recorded pair ever leaves the laptop; the raw file stays
local. `experiments/shot-improvement/core/cloud_sync.py` uploads a new
annotated clip right after it's recorded and deletes the cloud copy
when that clip is deleted locally, so the bucket mirrors the laptop's
own annotated clips rather than accumulating as a permanent archive.
Deletion is driven by a small local tombstone file
(`cloud_sync_tombstones.json`, gitignored), not by "missing locally
therefore delete remotely" — seeing an object's name in the bucket but
not (yet) in a local directory snapshot can't be told apart from a
mid-flight upload, so only a name this app was explicitly told to
delete is ever removed. One background thread, fed by a queue,
serializes every sync action (a startup reconcile, each post-recording
upload, each delete) so the GCS client is never touched from two
threads at once.

Server side (`server/shot_improvement_videos.py`, `server/main.py`):

- `GET /shot-improvement` / `GET /shot-improvement/app.js` — one
  combined React + Bootstrap page (sign-in button or clip gallery,
  depending on `whoami`), served via explicit routes rather than a
  `StaticFiles(html=True)` mount — see "Out of scope" for why.
- `GET /api/shot-improvement/{config,whoami}`,
  `POST /api/shot-improvement/{login,logout}` — same shape as
  Valkoapila's equivalents.
- `GET /api/shot-improvement/videos` — the gated clip list (name, size,
  recorded-at, newest first).
- `GET /api/shot-improvement/videos/{name}` — the gated mp4 bytes.
  `name` must match `^shot-improvement-\d{14}-annotated\.mp4$` before
  it ever reaches GCS. Downloads the blob once into a small per-instance
  cache under `tempfile.gettempdir()` (capped at 10 files — Cloud Run's
  `/tmp` is RAM-backed) and serves it via `FileResponse`, so Starlette's
  own HTTP Range/`If-Range` handling applies — needed because Safari/
  iOS refuses to play `<video>` at all from a server that ignores a
  Range probe.

## Why

The clips only existed on one laptop, with no way to review a session
from a phone or show one to anyone else. The owner wants a private
gallery that updates itself — record a clip, it shows up on the site
automatically — without hand-copying files or maintaining two copies
that can drift apart.

## Out of scope and known constraints

- **Requires the same one-time GCP OAuth setup spec 074 already did** —
  no new manual console step, since the existing Client ID is reused.
  `SHOT_IMPROVEMENT_ALLOWED_EMAILS`/`_SESSION_SECRET` are new Cloud Run
  env var/secret, following the existing fail-closed-when-unset
  convention.
- **Requires `gcloud auth application-default login` on the laptop**
  for `core/cloud_sync.py` to authenticate to GCS. Checked during this
  work and found **not actually configured** on this machine (despite
  an earlier assumption that it was) — sync fails with a caught,
  logged `DefaultCredentialsError` until this one-time interactive step
  is run; the GUI itself is unaffected (recording/playback/deletion all
  work regardless of sync status).
- **Not a StaticFiles mount at `/shot-improvement`**, unlike every
  other experiment (`/liiga`, `/valkoapila`, `/jos-olisi-jatkanut-
  prospects`). `docs/gcp-notes.md` records that hitting a mount's bare
  path (no trailing slash) 307-redirects to a scheme-blind, plain
  `http://` URL, because Cloud Run terminates TLS in front of the app.
  Two explicit routes (the page, `app.js`) sidestep this entirely.
- **Raw clips never leave the laptop** — only the annotated video syncs.
  Rewatching the unannotated original still means using the laptop
  directly.
- **The cloud copy is a mirror, not a backup/archive** — deleting a
  clip locally deletes it from the site too. There's no undelete.
- **No signed URLs** — video bytes are proxied through the FastAPI
  app rather than served directly from GCS, since signing from Cloud
  Run's default compute service account would need the IAM SignBlob
  API plus `roles/iam.serviceAccountTokenCreator` granted to itself,
  and would behave differently again against local-dev ADC. Proxying is
  fine at this scale — clips are 150 KB–2 MB, one viewer, far under
  Cloud Run's 32 MiB response cap.
- **No thumbnails, no metadata beyond size/recorded-at** — the gallery
  is a flat, newest-first list of `<video controls>` cards.

## Acceptance criteria

1. `plan_sync(local_names, remote_names, tombstones)` is a pure
   function: returns clips present locally but neither remotely nor
   tombstoned as `to_upload`, and tombstoned clips still present
   remotely as `to_delete` — critically, a clip present remotely but
   absent from `local_names` and not tombstoned is in neither set (the
   race a naive "absence implies delete" rule would get wrong).
   `[T-087-01]`
2. `GET /shot-improvement` and `GET /shot-improvement/app.js` return
   200 HTML/JS with no session, and no 307 redirect on the bare path.
   `[T-087-02]`
3. `POST /api/shot-improvement/login` with a valid Google ID token
   whose email is in `SHOT_IMPROVEMENT_ALLOWED_EMAILS` and
   `email_verified: true` sets a signed session cookie and returns 200;
   it's rejected (403) for a non-allowlisted or unverified email or a
   token that fails verification, and closed (503) when any of the
   three settings is unset. `[T-087-03]`
4. `GET /api/shot-improvement/videos` and
   `GET /api/shot-improvement/videos/{name}` both 401 without a valid
   session (missing, tampered, or no-longer-allowlisted-email cookie),
   and with one, return the GCS module's listing / that blob's bytes
   respectively (`Content-Type: video/mp4`). `[T-087-04]`
5. `GET /api/shot-improvement/videos/{name}` returns 404 for any name
   not matching the upload pattern, before ever calling into GCS.
   `[T-087-05]`
6. Recording a clip in the GUI uploads its annotated file to the bucket
   in the background without blocking the UI; deleting an annotated
   recording from the GUI's list deletes the same object from the
   bucket. (Verified live/manually — see "Implemented".)

## Test plan

- `[T-087-01]`: `experiments/shot-improvement/tests/test_cloud_sync.py`
  — pure-function cases only, matching this test directory's existing
  zero-mock/zero-fixture style.
- `[T-087-02]`–`[T-087-05]`:
  `server/tests/test_shot_improvement_routes.py`, mirroring
  `test_valkoapila_routes.py` — Google verification and
  `shot_improvement_videos.{list_videos,get_cached_path}` monkeypatched,
  the real HMAC cookie signing/verification exercised directly.
- Criterion 6: verified live against the real camera/bucket after
  deploy (see "Implemented").

## Implemented

- `server/config.py` — three new settings, all fail-closed when unset.
- `server/shot_improvement_videos.py` — the GCS read seam (list/cached-
  download), lazy client same pattern as `server/shares.py`. Kept
  server-side rather than importing anything from `experiments/shot-
  improvement/core/`, which pulls in cv2/mediapipe — those are
  deliberately excluded from `server/requirements.txt`.
- `server/main.py` — the routes and page described above, added
  `shot_improvement_videos` to the top-level import line, all
  registered before the existing `/liiga`/`/jos-olisi-jatkanut-
  prospects`/`/valkoapila` mounts (unaffected by this change — smoke-
  tested live post-deploy).
- `server/valkoapila_auth.py` — docstring updated to note it's now
  shared by this feature; no behaviour change.
- `experiments/shot-improvement/web/{index.html,app.js}` — the combined
  sign-in/gallery page, Bootstrap 5 + React 18 UMD + Babel Standalone,
  same stack as Valkoapila's pages.
- `experiments/shot-improvement/core/cloud_sync.py` — `plan_sync` (pure),
  `SyncWorker` (one background thread, a queue, tombstone-file
  read/write), `upload`/`delete`/`list_remote_names` (thin GCS calls).
- `experiments/shot-improvement/gui.py` — wired into
  `_on_recording_done` (upload after encode), `on_delete_selected`
  (delete-sync for the annotated file only — the raw file's row has no
  cloud counterpart), and startup (`SyncWorker.start_reconcile`).
  Status-line feedback in Finnish, matching the existing convention.
- `experiments/shot-improvement/requirements.txt` — added
  `google-cloud-storage==2.19.0` (same pin as `server/requirements.txt`)
  and `pytest==8.4.2` (the README already documented running pytest
  here; it was never actually listed).
- `.gitignore`/`.dockerignore` — tombstone file gitignored; the
  recordings/models/logs directories (already gitignored) were not
  previously dockerignored, so they'd have been uploaded into the
  build context and baked into the image. Fixed alongside this work.

**Infrastructure**, created via `gcloud` during this work:

- GCS bucket `wide-exchanger-463707-c6-shot-improvement`
  (europe-north1, uniform bucket-level access, public access prevention
  enforced). No extra IAM grant needed — the `ai-web` Cloud Run
  revision's service account already holds project `roles/editor`.
- Secret Manager secret `SHOT_IMPROVEMENT_SESSION_SECRET` (32 random
  bytes), with `roles/secretmanager.secretAccessor` granted to
  `3384713420-compute@developer.gserviceaccount.com` (needed even
  though that account has project Editor — Secret Manager access
  requires its own IAM binding).
- Deployed via `gcloud builds submit` + `gcloud run deploy` with
  `--update-env-vars`/`--update-secrets` (never `--set-*`, which would
  have replaced the whole existing set) — verified afterward with
  `gcloud run services describe ...` that every pre-existing env var
  survived, and live-smoke-tested that `/`, `/liiga`, `/valkoapila`,
  and `/jos-olisi-jatkanut-prospects` all still serve correctly.

Full pytest suite passing in both venvs: root repo 515 passed / 2
skipped (pre-existing live/opt-in tests) — no regressions; the
shot-improvement venv's own suite 35 passed.

**Known gap, not yet resolved**: `gcloud auth application-default
login` was assumed already done on this laptop but, checked directly
during this work, is not. Until it's run, the laptop app's cloud sync
will fail (caught, logged, does not affect recording/playback/delete)
rather than actually upload/delete anything. The gallery page and API
are otherwise live and verified end-to-end with routes, auth, and infra
all in place.
