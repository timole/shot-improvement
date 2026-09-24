# Azure deployment notes

Moved out of the README (spec 124). Live setup of the web gallery.

## Resources

Set up during specs 095/096, live in Azure subscription "Azure
subscription 1" (Pay-As-You-Go), resource group `shot-improvement`,
Sweden Central:

- Storage account `shotimprovement`, container `clips` (Standard_LRS,
  Hot, no public blob access). Laptop writes via `az login` +
  `DefaultAzureCredential` (`Storage Blob Data Contributor` on the
  signed-in account); the server's Container App system-assigned
  managed identity has **both** `Storage Blob Data Reader` (the
  original desktop-gallery read path) **and**, since spec 147,
  `Storage Blob Data Contributor` too - added so `POST
  /api/android/upload` can write the Android app's own shots under the
  `android/` blob-name prefix in the same container. No stored secret
  either way for Azure itself; `az role assignment create` failed
  outright in this environment for both roles (`MissingSubscription`,
  a CLI-specific quirk, not a real permissions gap -
  `Microsoft.Authorization` was confirmed registered and `az role
  definition list` worked fine) - worked around with a direct ARM
  REST call instead: `az rest --method PUT --url
  "https://management.azure.com/<scope>/providers/Microsoft.Authorization/roleAssignments/<new-guid>?api-version=2022-04-01"
  --body '{"properties":{"roleDefinitionId":"<role-id>","principalId":"<principal-id>","principalType":"ServicePrincipal"}}'`.
- The Android app's own upload auth is a separate, simple shared
  secret - `SHOT_ANDROID_UPLOAD_TOKEN` (Container App secret
  `android-upload-token`), checked in `server/main.py`'s
  `_check_upload_token` against the phone's `Authorization: Bearer
  ...` header. Not a SAS token straight to Blob Storage (the first
  design considered) - a **user delegation SAS is capped at 7 days**
  by Azure itself, useless for a standalone app with no way to fetch a
  fresh one; a classic account-key SAS would work but means enabling
  shared-key access and putting a storage-level credential in the
  APK. Routing uploads through the server instead keeps every Azure
  credential server-side, same as the rest of this project - rotating
  the phone's access is just `az containerapp secret set` plus a new
  APK build, no Azure-side token management at all. The token itself
  is also hardcoded into the Android app
  (`CloudUploader.UPLOAD_TOKEN`) - acceptable for a personal,
  side-loaded app (see that file's own doc comment) but would not be
  for a publicly distributed one.
- Container Registry `shotimprovementacr`, Container Apps environment
  `shot-improvement-env` + app `shot-improvement-server`. The Azure
  resource names themselves (this one, the storage account, the
  resource group) still say "shot" - spec 153's Shot -> Snapshot
  rename covers everything user-facing (app name/icon, web branding,
  the domain) but deliberately not these: Azure doesn't rename a
  storage account, container registry or resource group in place,
  only recreate-and-migrate, which is a lot of real risk/effort for
  something nobody but the owner ever sees the name of.
- Custom domain `snapshot.timolehtonen.tech` bound with a managed
  (DigiCert) certificate - DNS at Vercel (`vercel dns add`): the
  domain's existing CAA records needed a `0 issue "digicert.com"`
  entry added alongside the GCP-side `pki.goog` one, or issuance
  fails. `shot.timolehtonen.tech` (the original domain) is still
  bound too, with its own still-valid cert, and still serves the app
  fine - kept live deliberately as a transition safety net rather
  than torn down in the same spec that changed the primary domain.
  `server/main.py`'s `REDIRECT_URI` now points at the new domain only
  (the Entra ID App Registration has both redirect URIs registered,
  see below), so signing in from the old domain still completes, just
  lands you on the new one afterwards. Retire the old domain
  (`az containerapp hostname delete`, remove its DNS records and its
  redirect URI from the App Registration) once nothing still links to
  it.
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
- Spec 149's composed Android videos (video + audio + spectrogram, one
  .mp4 - see `server/android_compose.py`) need `ffmpeg`, added via the
  `imageio-ffmpeg` PyPI package (bundles a static binary for the
  container's platform) rather than an `apt-get` in the Dockerfile -
  no system package, no root step, just another `requirements.txt`
  line. Hand-position boxes (the yellow squares) are NOT rendered by
  the server at all - MediaPipe pose detection is too slow to run
  inline in a request (tens of ms/frame); `tools/compose_android_shots.py`
  runs that offline on the desktop (full `core/` + mediapipe available
  there) and uploads the result to `android/<stem>-composed.mp4`,
  which `android_compose.compose_video()` prefers over its own
  ffmpeg-only on-the-fly fallback whenever one exists.


## Redeploy

```
az acr build --registry shotimprovementacr --image shot-improvement-server:<tag> --file server/Dockerfile --platform linux/amd64 .
az containerapp update -n shot-improvement-server -g shot-improvement --image shotimprovementacr.azurecr.io/shot-improvement-server:<tag>
```
