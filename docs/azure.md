# Azure deployment notes

Moved out of the README (spec 124). Live setup of the web gallery.

## Resources

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


## Redeploy

```
az acr build --registry shotimprovementacr --image shot-improvement-server:<tag> --file server/Dockerfile --platform linux/amd64 .
az containerapp update -n shot-improvement-server -g shot-improvement --image shotimprovementacr.azurecr.io/shot-improvement-server:<tag>
```
