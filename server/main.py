"""FastAPI gallery for snapshot.timolehtonen.tech (spec 095/096) - v1: sign
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
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from . import android_blobs, android_compose, auth, blob_videos, config

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
REDIRECT_URI = "https://snapshot.timolehtonen.tech/api/login/callback"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# The page shell, app.js and the install page change with every deploy but
# have no fingerprint in their URL. Without Cache-Control a browser may
# reuse its copy for hours or days by heuristic (a phone kept showing the
# page without a new banner). no-cache = always revalidate; the ETag keeps
# that to a tiny 304.
REVALIDATE = {"Cache-Control": "no-cache"}


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


def _check_upload_token(request: Request) -> None:
    """Spec 147: the Android app's own auth for POST /api/android/upload -
    a plain shared secret, not a session cookie (the phone never does the
    browser-based Microsoft sign-in _session_email checks). compare_digest,
    not `==` - a timing side-channel on a long-lived bearer token is worth
    avoiding even though the actual attack surface here is small."""
    authz = request.headers.get("Authorization", "")
    token = authz[len("Bearer "):] if authz.startswith("Bearer ") else ""
    if not token or not secrets_module.compare_digest(token, config.ANDROID_UPLOAD_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid upload token.")


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


@app.post("/api/android/upload")
async def android_upload(
    request: Request,
    stem: str = Form(...),
    video: Optional[UploadFile] = File(None),
    audio: Optional[UploadFile] = File(None),
    metadata: Optional[UploadFile] = File(None),
) -> dict:
    """Spec 147: one shot's video/audio/metadata from the Android app,
    straight after it's decoded locally - none of these three are
    required (spec 140: a shot with no camera that session has no
    video), but at least one actual part should normally be present."""
    _check_upload_token(request)
    if not android_blobs.is_valid_stem(stem):
        raise HTTPException(status_code=400, detail="Invalid stem.")
    try:
        video_bytes = await video.read() if video is not None else None
        audio_bytes = await audio.read() if audio is not None else None
        metadata_bytes = await metadata.read() if metadata is not None else None
        android_blobs.upload_shot(stem, video_bytes, audio_bytes, metadata_bytes)
    except Exception:
        logger.exception("android_upload: failed for %s", stem)
        raise HTTPException(status_code=503, detail="Upload failed.")
    return {"ok": True}


@app.get("/api/android/videos")
def android_videos_list(request: Request) -> dict:
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    try:
        return {"shots": android_blobs.list_shots()}
    except Exception:
        logger.exception("Failed to list android shots")
        raise HTTPException(status_code=503, detail="Listaus epäonnistui.")


@app.get("/api/android/videos/{stem}")
def android_video_bytes(stem: str, request: Request) -> FileResponse:
    # `stem` is checked against android_blobs.STEM_RE before it ever reaches
    # Azure, same guard as GET /api/videos/{name} uses for the desktop clips.
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    if not android_blobs.is_valid_stem(stem):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    try:
        path = android_blobs.get_cached_path(stem, "mp4")
    except (ResourceNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    except Exception:
        logger.exception("Failed to fetch android video %s", stem)
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/api/android/audio/{stem}")
def android_audio_bytes(stem: str, request: Request) -> FileResponse:
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    if not android_blobs.is_valid_stem(stem):
        raise HTTPException(status_code=404, detail="Ääntä ei löytynyt.")
    try:
        path = android_blobs.get_cached_path(stem, "wav")
    except (ResourceNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=404, detail="Ääntä ei löytynyt.")
    except Exception:
        logger.exception("Failed to fetch android audio %s", stem)
        raise HTTPException(status_code=404, detail="Ääntä ei löytynyt.")
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/api/android/composed/{stem}")
def android_composed_bytes(stem: str, request: Request) -> FileResponse:
    """Spec 149: video + audio + spectrogram (and, once
    tools/compose_android_shots.py has processed the shot, hand-position
    boxes) combined into one .mp4 - what the web gallery's single-shot
    view actually plays, instead of juggling separate elements. Same
    session gate and stem validation as every other android/ route."""
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    if not android_blobs.is_valid_stem(stem):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    try:
        path = android_compose.compose_video(stem)
    except (ResourceNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    except Exception:
        logger.exception("Failed to compose android video %s", stem)
        raise HTTPException(status_code=503, detail="Videon koostaminen epäonnistui.")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/api/android/composed-meta/{stem}")
def android_composed_meta(stem: str, request: Request) -> dict:
    """Spec 149: the composed video's own duration, frame rate and real
    frame count - the web player's "one frame" step needs the real fps
    (an HTML5 <video> element exposes duration but not fps), and the
    "ruutu N/count" readout (matching MainActivity's own VideoFrameBox)
    needs the real count, not a derived one (round(duration*fps) was
    off by one on a real file - see probe_frame_count's own docstring).
    All cheap once the file is already cached (compose_video is
    idempotent)."""
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    if not android_blobs.is_valid_stem(stem):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    try:
        path = android_compose.compose_video(stem)
        return {
            "duration_s": android_compose.probe_duration_s(path),
            "fps": android_compose.probe_fps(path),
            "frame_count": android_compose.probe_frame_count(path),
        }
    except (ResourceNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=404, detail="Videota ei löytynyt.")
    except Exception:
        logger.exception("Failed to read android composed metadata %s", stem)
        raise HTTPException(status_code=503, detail="Videon tietojen luku epäonnistui.")


@app.get("/api/android/thumbnail/{stem}")
def android_thumbnail_bytes(stem: str, request: Request) -> FileResponse:
    """Spec 149: a single preview frame (or, for an audio-only shot, the
    spectrogram image) - the small preview both the web list and
    MainActivity's own Historia rows show."""
    if _session_email(request) is None:
        raise HTTPException(status_code=401, detail="Kirjaudu sisään.")
    if not android_blobs.is_valid_stem(stem):
        raise HTTPException(status_code=404, detail="Kuvaa ei löytynyt.")
    try:
        path = android_compose.thumbnail(stem)
    except (ResourceNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=404, detail="Kuvaa ei löytynyt.")
    except Exception:
        logger.exception("Failed to build android thumbnail %s", stem)
        raise HTTPException(status_code=503, detail="Esikatselukuvan luonti epäonnistui.")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/")
def index_page() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers=REVALIDATE)


@app.get("/liikeratatallenteet")
@app.get("/puhelimen-laukaukset")
def sub_page() -> FileResponse:
    """Spec 148: both are the same page shell as GET / - app.js itself
    branches on window.location.pathname (see App()) to decide which of
    HomePage/GalleryPage/AndroidPage to render, the same way GET /android
    below is a distinct static page rather than a third branch of app.js
    (that one's public/unauthenticated, so it has to be separate)."""
    return FileResponse(WEB_DIR / "index.html", headers=REVALIDATE)


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(WEB_DIR / "app.js", media_type="text/javascript", headers=REVALIDATE)


@app.get("/android")
def android_page() -> FileResponse:
    """Spec 136: public install page for the Android app (no sign-in - the
    phone's browser has to be able to fetch the APK)."""
    return FileResponse(WEB_DIR / "android.html", headers=REVALIDATE)


@app.get("/app.apk")
def app_apk() -> Response:
    try:
        data = blob_videos.read_apk()
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Sovellusta ei ole julkaistu.")
    except Exception:
        logger.exception("Failed to fetch APK")
        raise HTTPException(status_code=503, detail="Sovelluksen lataus epäonnistui.")
    # no-store: the blob is replaced on every release, and a cached old
    # build would be installed again by a phone that re-downloads.
    return Response(
        data,
        media_type="application/vnd.android.package-archive",
        headers={
            "Content-Disposition": 'attachment; filename="shot-improvement.apk"',
            "Cache-Control": "no-store",
        },
    )
