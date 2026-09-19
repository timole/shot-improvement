"""FastAPI gallery for shot.timolehtonen.tech (spec 095/096) - v1: sign
in with a Microsoft account, list the annotated clips currently in
Azure Blob Storage, play one with a thumbnail, download it.

Originally ported from ai-timolehtonen-tech's /shot-improvement routes
(Google Sign-In) - spec 096 swapped the identity provider to Microsoft
Entra ID (a standard server-side OAuth 2.0 authorization-code flow,
not a client-side SDK) by explicit request: this service has no GCP
dependency anywhere, including for sign-in. See server/auth.py's
module docstring for the full mechanism.

A different origin means a different cookie jar and a fresh session
secret - sessions here are deliberately NOT shared with
ai.timolehtonen.tech's."""

from __future__ import annotations

import logging
import secrets as secrets_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from azure.core.exceptions import ResourceNotFoundError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from . import auth, blob_videos, config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger("shot_improvement_server")

app = FastAPI()

SESSION_COOKIE_NAME = "shot_session"
STATE_COOKIE_NAME = "shot_oauth_state"
# Fixed, not derived from the request - this exact string is what's
# registered on the Entra ID App Registration's redirect URI, and it
# must match exactly (this deployment only ever serves this one
# domain, so hardcoding it is simpler and safer than trusting request
# headers for it).
REDIRECT_URI = "https://shot.timolehtonen.tech/api/login/callback"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _session_email(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    email = auth.verify_session_token(token, config.SESSION_SECRET)
    if email is None:
        return None
    # Re-checked on every call, not just at login - revoking access
    # (removing an email from the allowlist and redeploying) takes
    # effect immediately, not just for new sign-ins.
    if email not in auth.parse_allowed_emails(config.ALLOWED_EMAILS):
        return None
    return email


@app.get("/api/login/start")
def login_start() -> RedirectResponse:
    # A random, single-use state value, round-tripped through Microsoft
    # unchanged and compared against this same short-lived cookie on
    # the way back (login_callback) - standard OAuth2 CSRF protection
    # for the authorization-code flow.
    state = secrets_module.token_urlsafe(24)
    authorize_url = (
        f"{auth.AUTHORIZE_URL}?client_id={config.MICROSOFT_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=openid%20email%20profile"
        "&response_mode=query"
        f"&state={state}"
    )
    response = RedirectResponse(authorize_url)
    response.set_cookie(STATE_COOKIE_NAME, state, max_age=600, httponly=True, secure=True, samesite="lax")
    return response


@app.get("/api/login/callback")
def login_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None) -> RedirectResponse:
    if error or not code:
        logger.warning("login_callback: Microsoft returned an error or no code: %r", error)
        return RedirectResponse("/?login_error=1")

    expected_state = request.cookies.get(STATE_COOKIE_NAME)
    if not expected_state or state != expected_state:
        logger.warning("login_callback: state mismatch (possible CSRF or expired attempt)")
        raise HTTPException(status_code=400, detail="Virheellinen kirjautumispyyntö - yritä uudelleen.")

    try:
        token_resp = httpx.post(
            auth.TOKEN_URL,
            data={
                "client_id": config.MICROSOFT_CLIENT_ID,
                "client_secret": config.MICROSOFT_CLIENT_SECRET,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=10.0,
        )
    except httpx.HTTPError:
        logger.exception("login_callback: token exchange request failed")
        raise HTTPException(status_code=503, detail="Kirjautuminen epäonnistui (verkkovirhe).")

    if token_resp.status_code != 200:
        logger.warning("login_callback: token exchange returned %d: %s", token_resp.status_code, token_resp.text[:500])
        raise HTTPException(status_code=403, detail="Kirjautuminen epäonnistui.")

    id_token = token_resp.json().get("id_token")
    if not id_token:
        raise HTTPException(status_code=403, detail="Kirjautuminen epäonnistui.")

    try:
        claims = auth.verify_microsoft_id_token(id_token, config.MICROSOFT_CLIENT_ID)
    except auth.InvalidMicrosoftToken as exc:
        logger.warning("login_callback: ID token verification failed: %s", exc)
        raise HTTPException(status_code=403, detail="Kirjautuminen epäonnistui.")

    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not email or email not in auth.parse_allowed_emails(config.ALLOWED_EMAILS):
        raise HTTPException(status_code=403, detail="Tätä tiliä ei ole hyväksytty.")

    session_token = auth.create_session_token(email, config.SESSION_SECRET)
    response = RedirectResponse("/")
    response.delete_cookie(STATE_COOKIE_NAME)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=auth.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@app.get("/api/whoami")
def whoami(request: Request) -> dict:
    email = _session_email(request)
    if email is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    return {"email": email}


@app.post("/api/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/api/videos")
def videos_list(request: Request) -> dict:
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    try:
        return {"videos": blob_videos.list_videos()}
    except Exception:
        logger.exception("Failed to list videos")
        raise HTTPException(status_code=503, detail="Videoiden listaus epäonnistui.")


@app.get("/api/status")
def status(request: Request) -> dict:
    """Spec 120: the laptop's live recording status plus this server's
    own clock (the page computes its countdown against it, so a browser
    whose clock is off still counts down correctly)."""
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    try:
        current = blob_videos.read_status()
    except Exception:
        logger.exception("Failed to read status")
        current = None
    return {
        "status": current,
        "server_now": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/videos/{name}")
def video_bytes(name: str, request: Request) -> FileResponse:
    # `name` is checked against VIDEO_NAME_RE before it ever reaches
    # Azure, so this can't be used to read an arbitrary blob out of the
    # container.
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    if not blob_videos.is_valid_video_name(name):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    try:
        path = blob_videos.get_cached_path(name)
    except (ResourceNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    except Exception:
        logger.exception("Failed to fetch video %s", name)
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    # FileResponse (not a raw byte Response) so Starlette's own Range/
    # If-Range/conditional-request handling applies - needed for
    # Safari/iOS, which refuses to play <video> at all from a server
    # that doesn't answer a Range probe. Filenames are immutable once
    # uploaded, so the response can be cached hard.
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/api/previews/{name}")
def preview_bytes(name: str, request: Request) -> FileResponse:
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    if not blob_videos.is_valid_preview_name(name):
        raise HTTPException(status_code=404, detail="Esikatselukuvaa ei löytynyt.")
    try:
        path = blob_videos.get_cached_path(name)
    except (ResourceNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="Esikatselukuvaa ei löytynyt.")
    except Exception:
        logger.exception("Failed to fetch preview %s", name)
        raise HTTPException(status_code=404, detail="Esikatselukuvaa ei löytynyt.")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/")
def index_page() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(WEB_DIR / "app.js", media_type="text/javascript")
