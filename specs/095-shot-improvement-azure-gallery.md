# 095 — Shot-improvement: Azure-hosted gallery at shot.timolehtonen.tech

**Superseded in part by [spec 096](096-shot-improvement-microsoft-entra-auth.md)**:
this spec's "Auth" decision below (Google Sign-In, ported) was replaced
the same day, before real users ever signed in, with Microsoft Entra
ID - see 096 for why and what changed. Everything else here (Azure
Blob Storage, Container Apps, the custom domain, the dual-write) is
unchanged and still accurate.

## What

A new, standalone gallery service - v1 functionally equivalent to the
existing `ai.timolehtonen.tech/shot-improvement` gallery (spec 087) -
now **live at `https://shot.timolehtonen.tech`**, hosted entirely on
Azure:

- **Azure Blob Storage** (`shotimprovement` storage account, `clips`
  container) is the data store, alongside the existing private GCS
  bucket - `core/cloud_sync.py` now dual-writes to both.
- **Azure Container Apps** (`shot-improvement-server`, Sweden Central)
  runs a new, self-contained FastAPI app (`server/`) in this same
  repo, ported from `ai-timolehtonen-tech`'s `/shot-improvement`
  routes + `valkoapila_auth.py`.
- **Google Sign-In**, same mechanism as the GCP gallery, gated to the
  owner's email, its own session cookie/secret (not shared with
  `ai.timolehtonen.tech`'s).

The eventual goal for this domain is video analysis (detecting the
stick/racket-to-ball audio impact, then measuring stick bend at that
instant - the long-standing, still-unspecced goal from specs
078/079/085) - **this spec is v1 only**: the gallery, nothing more.
Azure Blob Storage as the data store is the foundation that work will
build on later.

## Why

The user asked directly for an Azure-hosted service, with v1 scoped to
"shows the recordings like [the existing gallery] currently shows."
Landing on Azure now, with real data flowing to it, gives the later
analysis work a real substrate to build against instead of a paper
plan.

## Design decisions (confirmed with the user before building)

1. **Storage**: migrate to Azure Blob Storage as the primary store
   from day one, not just read the existing GCS bucket cross-cloud -
   `core/cloud_sync.py` dual-writes to both going forward, keeping
   `ai.timolehtonen.tech` working unchanged during the transition.
2. **Repo**: the new service lives in THIS repo
   (github.com/timole/shot-improvement), not inside
   `ai-timolehtonen-tech` - a new `server/` directory.
3. **Old gallery**: `ai.timolehtonen.tech/shot-improvement` keeps
   running as-is; retiring it is an explicit future step, not part of
   this build.
4. **Auth**: the same proven Google Sign-In + hand-rolled HMAC session
   cookie mechanism, ported (not shared in place - separate repos) into
   `server/auth.py`, under a **new, dedicated** Google OAuth Client ID
   for the `https://shot.timolehtonen.tech` origin and a **fresh**
   session secret (never shared with the GCP gallery's).
5. **v1 scope**: sign in, list clips, play with a thumbnail, download -
   exactly what the existing gallery does. Lists **annotated clips
   only** (matching today's behavior) - the raw clip is in the same
   Azure container (dual-write syncs both, per spec 094), just not
   surfaced in this gallery yet.

## Design - what a careful validation pass caught before building

A dedicated validation pass (checked against current Microsoft docs,
not assumed) corrected several points in the initial plan:

- **Container creation needs the control-plane command**
  (`az storage container-rm create`, not `az storage container
  create`) - the data-plane one needs a role assignment that doesn't
  exist yet at that point in the sequence.
- **Managed-identity + ACR pull is a real chicken-and-egg problem**:
  the identity doesn't exist until the Container App is created, and
  the app can't pull a private image until that identity has
  `AcrPull`. Fixed by creating with a public placeholder image first,
  granting roles, then updating to the real image.
- **Least privilege, not one role for everything**: the server gets
  `Storage Blob Data Reader` (read-only - it only ever serves, never
  writes); the laptop gets `Storage Blob Data Contributor` (it's the
  only writer).
- **`az acr build`'s progress-hook chunking gotcha**: `azure-storage-
  blob`'s default `max_single_put_size` (64 MiB) would upload a typical
  clip (a few hundred KB-2 MB) as ONE unchunked PUT, firing
  `progress_hook` almost once instead of smoothly - set to 256 KiB
  (matching `core.cloud_sync.UPLOAD_CHUNK_SIZE`) to restore the same
  progress granularity as the GCS path.
- **A real pre-existing race, caught while porting**: the GCS gallery's
  `get_cached_path()` downloaded straight into the final cache path -
  a second concurrent Range request against a cold cache (which a
  video player firing several parallel Range requests routinely
  triggers) could be served a half-written file. `server/
  blob_videos.py`'s port downloads to a `.part` path and atomically
  renames it into place instead.
- **CAA and the custom-domain sequence, verified against Microsoft's
  own docs (not guessed)**: Azure Container Apps' managed certificates
  are issued by DigiCert - confirmed the existing `pki.goog`/
  `sectigo.com`/`letsencrypt.org` CAA records needed a `digicert.com`
  entry added alongside them, or issuance/renewal fails outright.
  Exact DNS records (CNAME + `asuid.<subdomain>` TXT, both from live
  `az containerapp show` output) and the `az containerapp hostname
  add`/`bind --validation-method CNAME` sequence, all verified live.
- **`server/` must not import anything from `core/`** - `core/
  recorder.py` (and everything importing it) pulls in opencv-python
  and mediapipe, which a gallery server has no business needing. The
  small bits actually shared in spirit (filename regex, preview-name
  mapping) are duplicated in `server/blob_videos.py`, not imported.
- **A real bug caught by a test, not just review**: `core.cloud_sync
    ._reconcile()` originally called `_local_video_names()` with no
  argument, relying on that function's own default parameter - which
  is bound once at function-definition time, so patching the module-
  level `RECORDINGS_DIR` (as a test does) silently had no effect and
  reconcile would act on the REAL recordings directory regardless.
  Fixed by passing `RECORDINGS_DIR` explicitly at the call site;
  caught by `test_reconcile_uploads_only_to_the_backend_missing_a_name`
  actually reading real repo data before the fix was in.
- **Tombstone retirement across N backends**: naively discarding a
  tombstone as soon as ONE backend confirms deletion would orphan the
  name forever on any backend that hadn't caught up yet (down,
  slower, or simply added later). Fixed: a tombstone is only retired
  once it's confirmed absent from EVERY configured backend in the same
  reconcile pass - covered by
  `test_reconcile_retires_a_tombstone_only_once_gone_from_every_backend`.
- **ACR Tasks is disabled on Azure Free Trial subscriptions** - hit
  live (`TasksOperationsNotAllowed`), confirmed as a known, common
  restriction (Microsoft Q&A), resolved by upgrading to Pay-As-You-Go.

## Implemented

- `core/video_preview.py` (new): `generate_preview`/`preview_name`
  extracted out of `core/cloud_sync.py` so both it and
  `core/azure_sync.py` share one ffmpeg-based implementation.
- `core/cloud_sync.py`: generalized to a `Backend` protocol
  (`list_remote_names`/`upload_video`/`upload_preview`/`delete`).
  `GcsBackend` implements it (the original GCS logic, unchanged in
  spirit). `upload()`/`delete()` are now free functions taking a
  `backends` sequence - resilient (one backend failing doesn't stop
  the others), generate the preview once per upload regardless of
  backend count, and scale `on_progress` combined across all backends.
  `SyncWorker` takes `backends` at construction; `_reconcile()` calls
  `plan_sync()` once per backend (a name present on one backend but
  missing from another - e.g. mid-migration - is correctly uploaded to
  the one missing it) and only retires a tombstone once confirmed gone
  everywhere.
- `core/azure_sync.py` (new): `AzureBlobBackend`, same `Backend` shape,
  using `azure-storage-blob` + `azure-identity`'s
  `DefaultAzureCredential` (an `az login` session on the laptop, a
  system-assigned managed identity in the Container App - no stored
  secret either way).
- `gui.py`: `SyncWorker` now constructed with
  `backends=[GcsBackend(), AzureBlobBackend()]`.
- `server/` (new): `main.py` (routes), `auth.py` (ported from
  `valkoapila_auth.py`), `blob_videos.py` (ported from
  `shot_improvement_videos.py`, Azure Blob-backed, with the race-
  condition fix above), `config.py` (fail-fast env vars),
  `requirements.txt`/`requirements-dev.txt`, `Dockerfile`.
- `web/index.html`/`web/app.js`: updated to this service's own
  top-level `/api/...` paths (no `/shot-improvement` prefix - nothing
  else shares this domain).
- `.dockerignore` (new, repo had none) - `az acr build` has no
  `.gitignore` fallback the way `gcloud`'s `.gcloudignore` does.
- `requirements.txt`: added `azure-storage-blob`/`azure-identity` for
  the laptop's dual-write.
- Tests: `tests/test_video_preview.py`, `tests/test_cloud_sync.py`
  (+9 new: multi-backend upload/delete resilience, progress scaling,
  reconcile correctness), `server/tests/` (36 tests, mirroring
  `ai-timolehtonen-tech`'s route-test pattern plus new `auth.py`
  round-trip/tamper tests).

## Azure resources provisioned this session

Resource group `shot-improvement` (Sweden Central - closest region to
Finland with full Container Apps support): storage account
`shotimprovement` (Standard_LRS, Hot tier, blob-public-access
disabled) + container `clips`; Container Registry `shotimprovementacr`
(Basic); Container Apps environment `shot-improvement-env` + app
`shot-improvement-server` (0.5 vCPU/1GiB, system-assigned identity,
`AcrPull` + `Storage Blob Data Reader` on that identity, `Storage Blob
Data Contributor` granted to the user's own account for laptop
dual-write); custom domain `shot.timolehtonen.tech` bound with a
managed (DigiCert) certificate. DNS at Vercel (`vercel dns add`): CNAME
`shot`, TXT `asuid.shot`, and a `digicert.com` CAA entry added
alongside the existing GCP-side ones.

All 39 existing clips (+ their previews) synced to the new container in
this session - no separate migration script needed, since the source
of truth (all 39 files) was still present on the laptop's own
`recordings/` directory; a plain reconcile pass with the Azure backend
enabled did the whole "migration".

## Verified this session, end to end, against the live deployment

- `venv\Scripts\pytest tests\` - 63 passed. `server\.venv\Scripts\
  pytest server\tests\` - 36 passed.
- Laptop → Azure Blob write path: a real reconcile uploaded all 39
  clips + previews; container now holds 78 objects, confirmed via a
  direct listing.
- Deployed container confirmed NOT importing cv2/mediapipe (`server/`'s
  isolation from `core/` holds).
- `https://shot.timolehtonen.tech/api/config`, `/`, `/app.js` all
  return 200; `/api/whoami` correctly 401s without a session.
- `/api/videos` (with a manually-signed test session cookie) correctly
  lists all 19 annotated clips with the right names/sizes/timestamps.
- **Range requests work end-to-end through Container Apps' Envoy
  ingress**: `curl -r 0-1023` against a real video returned `HTTP/1.1
  206 Partial Content` with a correct `Content-Range` header - the one
  thing the validation pass flagged as "confirm empirically, not
  documented either way."
- TLS: `https://shot.timolehtonen.tech` serves over a trusted
  certificate (no `-k` needed).

## Out of scope / follow-up

- **Google OAuth Client ID is still a placeholder** on the deployed
  Container App secret - real sign-in via the browser button won't
  work until a real client ID (created for the
  `https://shot.timolehtonen.tech` origin, in Google Cloud Console -
  a manual, browser-only step) replaces it.
- **`min-replicas` is currently 1** (not 0/scale-to-zero) - set
  deliberately while the certificate was first issued (Microsoft's own
  docs: the app must stay running during issuance AND every renewal,
  ~45 days out). Whether to drop back to 0 (small cost saving, but a
  ~5-20s cold start on the first request after idling, and the same
  renewal risk resurfaces every ~45 days) is an open call for the
  user, not decided here.
- Retiring `ai.timolehtonen.tech/shot-improvement` - explicit future
  work (see decision #3 above), not started.
- Dropping the GCS half of `core/cloud_sync.py`'s dual-write - only
  once the above retirement actually happens.
- No video-analysis feature - the actual point of this domain
  eventually, entirely unbuilt, no persisted signal (pose landmarks,
  audio impact timestamp) exists anywhere yet to build it from.
