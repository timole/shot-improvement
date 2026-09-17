# 096 — Shot-improvement: swap the Azure gallery's sign-in to Microsoft Entra ID

## What

`shot.timolehtonen.tech` (spec 095) no longer uses Google Sign-In.
Sign-in is now Microsoft Entra ID, via a standard server-side OAuth 2.0
authorization-code flow: a "Kirjaudu Microsoft-tilillä" link redirects
to Microsoft, Microsoft redirects back with a `code`, the server
exchanges it for an ID token and verifies it against Microsoft's own
JWKS. Everything downstream of that - the session cookie, the email
allowlist check, all the video/preview routes - is unchanged.

## Why

The user's own words, asked directly when the (otherwise complete)
Google-based flow just needed a real OAuth Client ID created: "Let's
not use GCP at all. Only Azure." This service's whole point was
already "an Azure service" - the one remaining GCP touchpoint was
identity, not infrastructure, and it wasn't necessary: the account
this laptop already signs into Azure with, `timo.lehtonen@gmail.com`,
is itself a Microsoft-recognized identity (Azure subscriptions sign up
under a Microsoft account or an Entra ID account; a Gmail address used
as an MSA login is a normal, common setup), so no GCP registration was
ever actually needed for sign-in either.

## Design

- **A server-side redirect flow, not a client-side SDK.** The Google
  version loaded Google Identity Services' JS, got an ID token in the
  browser via a popup, and POSTed it to `/api/login`. The Microsoft
  version skips all of that: `GET /api/login/start` redirects the
  whole page to Microsoft; `GET /api/login/callback` receives the
  authorization `code` and does the token exchange itself
  (`httpx.post` to Microsoft's token endpoint). Simpler on the
  frontend (a plain `<a href="/api/login/start">`, no SDK script tag,
  no button-render callback, no POST) at the cost of two small new
  server routes instead of one.
- **CSRF protection via a `state` cookie**: `login_start` sets a
  random, single-use value in a short-lived (`max_age=600`) httponly
  cookie and embeds the same value in the redirect to Microsoft;
  `login_callback` rejects a mismatch before doing anything else.
  Standard practice for the authorization-code flow; the Google
  version's client-side-popup shape didn't need this, since Google
  Identity Services' own SDK handles that internally.
- **Multi-tenant App Registration, not pinned to one tenant**:
  `az ad app create --sign-in-audience AzureADandPersonalMicrosoftAccount`
  - accepts both organizational Entra accounts and personal Microsoft
  accounts (needed here, since the owner's account is personal/MSA,
  not an org account). The token verification (`server/auth.py`'s
  `verify_microsoft_id_token`) can't pin one expected issuer as a
  result - it checks the issuer has the right *shape*
  (`https://login.microsoftonline.com/<tenant-guid>/v2.0`) rather than
  one fixed value, and leans on the email allowlist check (applied
  right after, in `server/main.py`) as the actual access boundary -
  same division of responsibility the Google version already had
  (`email_verified` + allowlist, not the issuer, was the real gate
  there too).
- **A confidential client (client secret), not PKCE.** Since this is
  a real server-side app (not a browser-only SPA), a standard
  confidential-client authorization-code exchange is simpler to set up
  correctly than a public-client PKCE flow would be, and avoids ever
  needing a client-side auth library (MSAL.js) at all.
- **`REDIRECT_URI` is a fixed constant**, not derived from the
  incoming request - this deployment only ever serves
  `shot.timolehtonen.tech`, and the value must match the App
  Registration's registered redirect URI exactly; trusting a request
  header for it would be both unnecessary and a bit less safe.

## Implemented

- Entra ID App Registration `shot-improvement`
  (`AzureADandPersonalMicrosoftAccount`, redirect URI
  `https://shot.timolehtonen.tech/api/login/callback`), with a 2-year
  client secret.
- `server/auth.py`: `verify_google_id_token` → `verify_microsoft_id_token`
  (PyJWT + `jwt.PyJWKClient` against Microsoft's JWKS, audience +
  issuer-shape checks). Session-cookie code (`create_session_token`/
  `verify_session_token`/`parse_allowed_emails`) unchanged.
- `server/config.py`: `GOOGLE_CLIENT_ID` → `MICROSOFT_CLIENT_ID` +
  new `MICROSOFT_CLIENT_SECRET` (`SHOT_MICROSOFT_CLIENT_ID`/
  `SHOT_MICROSOFT_CLIENT_SECRET` env vars).
- `server/main.py`: `POST /api/login` (ID-token body) + `GET
  /api/config` replaced with `GET /api/login/start` + `GET
  /api/login/callback`; `POST /api/logout` now redirects home (303)
  to match the page-navigation shape of the rest of this flow.
- `web/index.html`: dropped the Google Identity Services `<script>`
  tag.
- `web/app.js`: `LoginCard` is now a plain link, not a rendered Google
  button; no client-side SDK init/callback code left at all.
- `server/requirements.txt`: `google-auth` → `httpx` (token exchange)
  + `PyJWT[crypto]` (ID token verification).
- Azure Container App: new `microsoft-client-id`/
  `microsoft-client-secret` secrets, wired to `SHOT_MICROSOFT_CLIENT_ID`/
  `SHOT_MICROSOFT_CLIENT_SECRET`; old `google-client-id` secret and
  `SHOT_GOOGLE_CLIENT_ID` env var removed.
- Tests: `server/tests/test_auth.py` gained
  `verify_microsoft_id_token` coverage (valid org issuer, valid
  personal-account issuer, rejected issuer, wrapped decode failure -
  JWKS fetch/signature verification themselves mocked, not this
  module's logic to test). `server/tests/test_routes.py`'s login tests
  rewritten for the redirect/callback shape (state-mismatch rejection,
  Microsoft-side error redirect, token-exchange failure, network
  failure during exchange, in addition to the existing allowlist/
  invalid-token cases).

## Test plan

- `server\.venv\Scripts\pytest server\tests\` - 43 passed (7 new in
  `test_auth.py`, login tests in `test_routes.py` rewritten).
- Live, against the real deployment: `GET
  https://shot.timolehtonen.tech/api/login/start` correctly
  307-redirects to `login.microsoftonline.com` with the real
  (non-placeholder) client ID and the exact registered redirect URI;
  `GET /api/config` correctly 404s (route removed, nothing needs it
  client-side anymore); existing session cookies (unaffected by this
  change) still work against `/api/whoami` and `/api/videos`.
- Not verified this session: an actual end-to-end browser sign-in
  (requires a real interactive Microsoft login) - the redirect
  construction, token-exchange call shape, and ID-token verification
  logic are all covered above, but the very first real click-through
  is still open to confirm.
