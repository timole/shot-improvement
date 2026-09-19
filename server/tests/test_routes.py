"""Tests for the gallery's gated API (spec 095/096) - login/callback,
whoami, videos(/{name}), previews/{name}, and the public page shell.
Microsoft ID token verification and Azure Blob access
(server/blob_videos.py) are both mocked; the session-cookie signing/
verification code itself runs for real."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient

from server import auth
from server.main import REDIRECT_URI, SESSION_COOKIE_NAME, STATE_COOKIE_NAME, app

client = TestClient(app, follow_redirects=False)

ALLOWED_EMAIL = "timo.lehtonen@gmail.com"


def _session_cookie() -> str:
    import server.config as config

    return auth.create_session_token(ALLOWED_EMAIL, config.SESSION_SECRET)


def _mock_token_exchange(monkeypatch: pytest.MonkeyPatch, id_token: str = "fake-id-token") -> None:
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"id_token": id_token}
    monkeypatch.setattr("server.main.httpx.post", lambda *a, **kw: fake_response)


def _mock_claims(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    monkeypatch.setattr(
        "server.main.auth.verify_microsoft_id_token",
        lambda token, client_id: {"email": email, "iss": "https://login.microsoftonline.com/some-tenant/v2.0"},
    )


# --- GET /api/login/start -------------------------------------------------


def test_login_start_redirects_to_microsoft_with_a_state_cookie() -> None:
    resp = client.get("/api/login/start")
    assert resp.status_code == 307  # RedirectResponse's default
    assert resp.headers["location"].startswith(auth.AUTHORIZE_URL)
    assert "client_id=test-client-id" in resp.headers["location"]
    assert STATE_COOKIE_NAME in resp.cookies


# --- GET /api/login/callback -----------------------------------------------


def test_login_callback_success_sets_a_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_token_exchange(monkeypatch)
    _mock_claims(monkeypatch, ALLOWED_EMAIL)

    resp = client.get(
        "/api/login/callback",
        params={"code": "fake-code", "state": "matching-state"},
        cookies={STATE_COOKIE_NAME: "matching-state"},
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "/"
    assert SESSION_COOKIE_NAME in resp.cookies


def test_login_callback_rejects_a_state_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_token_exchange(monkeypatch)
    _mock_claims(monkeypatch, ALLOWED_EMAIL)

    resp = client.get(
        "/api/login/callback",
        params={"code": "fake-code", "state": "attacker-supplied-state"},
        cookies={STATE_COOKIE_NAME: "the-real-state"},
    )
    assert resp.status_code == 400
    assert SESSION_COOKIE_NAME not in resp.cookies


def test_login_callback_redirects_home_with_an_error_when_microsoft_reports_one() -> None:
    resp = client.get("/api/login/callback", params={"error": "access_denied"})
    assert resp.status_code == 307
    assert resp.headers["location"] == "/?login_error=1"


def test_login_callback_rejects_email_not_in_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_token_exchange(monkeypatch)
    _mock_claims(monkeypatch, "someone.else@example.com")

    resp = client.get(
        "/api/login/callback",
        params={"code": "fake-code", "state": "s"},
        cookies={STATE_COOKIE_NAME: "s"},
    )
    assert resp.status_code == 403
    assert SESSION_COOKIE_NAME not in resp.cookies


def test_login_callback_rejects_an_invalid_id_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_token_exchange(monkeypatch)

    def raise_invalid(token, client_id):
        raise auth.InvalidMicrosoftToken("bad signature")

    monkeypatch.setattr("server.main.auth.verify_microsoft_id_token", raise_invalid)

    resp = client.get(
        "/api/login/callback",
        params={"code": "fake-code", "state": "s"},
        cookies={STATE_COOKIE_NAME: "s"},
    )
    assert resp.status_code == 403


def test_login_callback_handles_token_exchange_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = MagicMock()
    fake_response.status_code = 400
    fake_response.text = "invalid_grant"
    monkeypatch.setattr("server.main.httpx.post", lambda *a, **kw: fake_response)

    resp = client.get(
        "/api/login/callback",
        params={"code": "fake-code", "state": "s"},
        cookies={STATE_COOKIE_NAME: "s"},
    )
    assert resp.status_code == 403


def test_login_callback_handles_a_network_error_during_token_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_network_error(*a, **kw):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr("server.main.httpx.post", raise_network_error)

    resp = client.get(
        "/api/login/callback",
        params={"code": "fake-code", "state": "s"},
        cookies={STATE_COOKIE_NAME: "s"},
    )
    assert resp.status_code == 503


# --- GET /api/whoami -------------------------------------------------------


def test_whoami_requires_a_session() -> None:
    resp = client.get("/api/whoami")
    assert resp.status_code == 401


def test_whoami_returns_the_email() -> None:
    resp = client.get("/api/whoami", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.json() == {"email": ALLOWED_EMAIL}


def test_rejects_a_tampered_cookie() -> None:
    resp = client.get("/api/videos", cookies={SESSION_COOKIE_NAME: "not.a-valid-token"})
    assert resp.status_code == 401


def test_rejects_an_email_removed_from_the_allowlist_after_login(monkeypatch: pytest.MonkeyPatch) -> None:
    cookie = _session_cookie()
    monkeypatch.setattr("server.main.config.ALLOWED_EMAILS", "someone.else@example.com")
    resp = client.get("/api/videos", cookies={SESSION_COOKIE_NAME: cookie})
    assert resp.status_code == 401


# --- POST /api/logout --------------------------------------------------


def test_logout_clears_the_session() -> None:
    resp = client.post("/api/logout", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 303
    assert "Max-Age=0" in resp.headers.get("set-cookie", "")


# --- GET /api/videos ---------------------------------------------------


def test_videos_list_requires_a_session() -> None:
    resp = client.get("/api/videos")
    assert resp.status_code == 401


def test_videos_list_returns_what_blob_videos_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_videos = [
        {"name": "shot-improvement-20260911182712-annotated.mp4", "size": 152048, "recorded_at": "2026-09-11T18:27:12"},
    ]
    monkeypatch.setattr("server.main.blob_videos.list_videos", lambda: fake_videos)
    resp = client.get("/api/videos", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.json() == {"videos": fake_videos}


def test_videos_list_returns_503_on_azure_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error():
        raise RuntimeError("container unreachable")

    monkeypatch.setattr("server.main.blob_videos.list_videos", raise_error)
    resp = client.get("/api/videos", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 503


# --- GET /api/videos/{name} ---------------------------------------------

VALID_NAME = "shot-improvement-20260911182712-annotated.mp4"


def test_video_bytes_requires_a_session() -> None:
    resp = client.get(f"/api/videos/{VALID_NAME}")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "shot-improvement-20260911182712.mp4",  # raw, not annotated - not listed in v1
        "not-even-a-video-name.mp4",
    ],
)
def test_video_bytes_rejects_a_name_that_doesnt_match_the_upload_pattern(name: str) -> None:
    resp = client.get(f"/api/videos/{name}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 404


def test_video_bytes_are_served_with_a_valid_session_and_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_video = tmp_path / VALID_NAME
    fake_video.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-mp4-bytes")
    monkeypatch.setattr("server.main.blob_videos.get_cached_path", lambda name: fake_video)

    resp = client.get(f"/api/videos/{VALID_NAME}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == fake_video.read_bytes()


def test_video_bytes_returns_404_when_not_found_in_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name):
        raise ResourceNotFoundError("not found")

    monkeypatch.setattr("server.main.blob_videos.get_cached_path", raise_not_found)
    resp = client.get(f"/api/videos/{VALID_NAME}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 404


# --- GET /api/previews/{name} -------------------------------------------

VALID_PREVIEW_NAME = "shot-improvement-20260911182712-annotated.jpg"


def test_preview_bytes_requires_a_session() -> None:
    resp = client.get(f"/api/previews/{VALID_PREVIEW_NAME}")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "shot-improvement-20260911182712.jpg",  # raw clip's preview - not listed in v1
        "shot-improvement-20260911182712-annotated.mp4",  # the video, not its preview
        "not-even-a-preview-name.jpg",
    ],
)
def test_preview_bytes_rejects_a_name_that_doesnt_match_the_upload_pattern(name: str) -> None:
    resp = client.get(f"/api/previews/{name}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 404


def test_preview_bytes_are_served_with_a_valid_session_and_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_preview = tmp_path / VALID_PREVIEW_NAME
    fake_preview.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    monkeypatch.setattr("server.main.blob_videos.get_cached_path", lambda name: fake_preview)

    resp = client.get(f"/api/previews/{VALID_PREVIEW_NAME}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == fake_preview.read_bytes()


def test_preview_bytes_returns_404_when_not_found_in_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name):
        raise ResourceNotFoundError("not found")

    monkeypatch.setattr("server.main.blob_videos.get_cached_path", raise_not_found)
    resp = client.get(f"/api/previews/{VALID_PREVIEW_NAME}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 404


# --- Public page shell -----------------------------------------------------


def test_page_serves_html_without_a_session() -> None:
    # No access check on the shell itself - app.js redirects to showing
    # the sign-in link client-side on a 401 from whoami.
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_app_js_is_served_without_a_session() -> None:
    resp = client.get("/app.js")
    assert resp.status_code == 200


def test_redirect_uri_constant_matches_the_registered_app() -> None:
    # This exact string must match the Entra ID App Registration's
    # redirect URI - a drift here fails silently as a Microsoft-side
    # "redirect_uri_mismatch" error, not a local test failure, so it's
    # worth pinning explicitly.
    assert REDIRECT_URI == "https://shot.timolehtonen.tech/api/login/callback"


# --- GET /api/status (spec 120) --------------------------------------------


def test_status_requires_a_session() -> None:
    assert client.get("/api/status").status_code == 401


def test_status_returns_the_laptops_status_and_server_time(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {"id": "20260919154426", "state": "processing", "fastest_kmh": 87}
    monkeypatch.setattr("server.main.blob_videos.read_status", lambda: fake)
    resp = client.get("/api/status", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == fake
    assert "server_now" in body


def test_status_is_null_when_nothing_published_or_azure_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("azure down")

    monkeypatch.setattr("server.main.blob_videos.read_status", boom)
    resp = client.get("/api/status", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.json()["status"] is None


def test_raw_clip_names_are_servable_but_not_listed() -> None:
    from server import blob_videos

    assert blob_videos.is_valid_video_name("shot-improvement-20260919154426.mp4")
    assert blob_videos.is_valid_video_name("shot-improvement-20260919154426-annotated.mp4")
    assert not blob_videos.is_valid_video_name("status/latest.json")
    assert not blob_videos.is_valid_video_name("../shot-improvement-20260919154426.mp4")
