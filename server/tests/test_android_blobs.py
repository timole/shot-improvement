"""Tests for the Android app's own upload/download surface (spec 147) -
POST /api/android/upload, GET /api/android/videos(/{stem}),
GET /api/android/audio/{stem}, and android_blobs.is_valid_stem/
list_shots' own row-merging. Azure Blob access (server/android_blobs.py)
is mocked throughout; only the upload-token check and the routing/
validation logic run for real."""

from __future__ import annotations

from pathlib import Path

import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient

from server import auth
from server.main import SESSION_COOKIE_NAME, app

client = TestClient(app, follow_redirects=False)

ALLOWED_EMAIL = "timo.lehtonen@gmail.com"
UPLOAD_TOKEN = "test-android-upload-token"  # matches conftest.py's SHOT_ANDROID_UPLOAD_TOKEN
VALID_STEM = "shot-20260922180735-1"


def _session_cookie() -> str:
    import server.config as config

    return auth.create_session_token(ALLOWED_EMAIL, config.SESSION_SECRET)


def _auth_header(token: str = UPLOAD_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- android_blobs.is_valid_stem --------------------------------------------


@pytest.mark.parametrize(
    "stem,expected",
    [
        ("shot-20260922180735-1", True),
        ("shot-20260922180735-12", True),
        ("dev-shot-20260922180735-1", False),  # spec 139/147: dev shots never upload
        ("../../etc/passwd", False),
        ("shot-2026-1", False),  # timestamp not 14 digits
        ("not-a-shot-at-all", False),
        ("", False),
    ],
)
def test_is_valid_stem(stem: str, expected: bool) -> None:
    from server import android_blobs

    assert android_blobs.is_valid_stem(stem) is expected


def test_list_shots_merges_video_and_audio_rows_by_stem(monkeypatch: pytest.MonkeyPatch) -> None:
    from server import android_blobs

    class FakeBlob:
        def __init__(self, name: str, size: int) -> None:
            self.name = name
            self.size = size

    fake_blobs = [
        FakeBlob("android/shot-20260922180735-1.mp4", 500_000),
        FakeBlob("android/shot-20260922180735-1.wav", 200_000),
        FakeBlob("android/shot-20260922180735-1.json", 300),
        FakeBlob("android/shot-20260922180800-2.wav", 190_000),  # audio-only (spec 140)
        FakeBlob("android/not-a-valid-stem.mp4", 1),
    ]

    class FakeContainer:
        def list_blobs(self, name_starts_with: str):
            assert name_starts_with == "android/"
            return fake_blobs

    monkeypatch.setattr(android_blobs, "_container", lambda: FakeContainer())

    shots = android_blobs.list_shots()

    assert len(shots) == 2  # not-a-valid-stem.mp4 silently skipped
    assert shots[0]["stem"] == "shot-20260922180800-2"  # newest first
    assert shots[0]["has_video"] is False
    assert shots[0]["has_audio"] is True
    assert shots[1]["stem"] == "shot-20260922180735-1"
    assert shots[1]["has_video"] is True
    assert shots[1]["has_audio"] is True
    assert shots[1]["video_size"] == 500_000


# --- POST /api/android/upload ------------------------------------------------


def test_upload_requires_the_bearer_token() -> None:
    resp = client.post("/api/android/upload", data={"stem": VALID_STEM})
    assert resp.status_code == 401


def test_upload_rejects_a_wrong_token() -> None:
    resp = client.post("/api/android/upload", data={"stem": VALID_STEM}, headers=_auth_header("wrong-token"))
    assert resp.status_code == 401


def test_upload_rejects_an_invalid_stem() -> None:
    resp = client.post("/api/android/upload", data={"stem": "../../etc/passwd"}, headers=_auth_header())
    assert resp.status_code == 400


def test_upload_writes_whichever_parts_are_present(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        "server.main.android_blobs.upload_shot",
        lambda stem, video, audio, metadata: calls.append((stem, video, audio, metadata)),
    )

    resp = client.post(
        "/api/android/upload",
        data={"stem": VALID_STEM},
        files={"video": ("v.mp4", b"fake-mp4-bytes", "video/mp4"), "audio": ("a.wav", b"fake-wav-bytes", "audio/wav")},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(calls) == 1
    stem, video, audio, metadata = calls[0]
    assert stem == VALID_STEM
    assert video == b"fake-mp4-bytes"
    assert audio == b"fake-wav-bytes"
    assert metadata is None  # spec 147: none of the three parts are required


def test_upload_returns_503_on_azure_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(stem, video, audio, metadata):
        raise RuntimeError("container unreachable")

    monkeypatch.setattr("server.main.android_blobs.upload_shot", raise_error)
    resp = client.post(
        "/api/android/upload",
        data={"stem": VALID_STEM},
        files={"video": ("v.mp4", b"x", "video/mp4")},
        headers=_auth_header(),
    )
    assert resp.status_code == 503


# --- GET /api/android/videos -------------------------------------------------


def test_android_videos_list_requires_a_session() -> None:
    assert client.get("/api/android/videos").status_code == 401


def test_android_videos_list_returns_what_android_blobs_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_shots = [{"stem": VALID_STEM, "recorded_at": "2026-09-22T18:07:35", "has_video": True, "has_audio": True, "video_size": 500_000}]
    monkeypatch.setattr("server.main.android_blobs.list_shots", lambda: fake_shots)
    resp = client.get("/api/android/videos", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.json() == {"shots": fake_shots}


def test_android_videos_list_returns_503_on_azure_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error():
        raise RuntimeError("container unreachable")

    monkeypatch.setattr("server.main.android_blobs.list_shots", raise_error)
    resp = client.get("/api/android/videos", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 503


# --- GET /api/android/videos/{stem} -----------------------------------------


def test_android_video_bytes_requires_a_session() -> None:
    assert client.get(f"/api/android/videos/{VALID_STEM}").status_code == 401


@pytest.mark.parametrize("stem", ["../../etc/passwd", "dev-shot-20260922180735-1", "not-a-shot"])
def test_android_video_bytes_rejects_an_invalid_stem(stem: str) -> None:
    resp = client.get(f"/api/android/videos/{stem}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 404


def test_android_video_bytes_are_served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_video = tmp_path / f"{VALID_STEM}.mp4"
    fake_video.write_bytes(b"fake-mp4-bytes")
    monkeypatch.setattr("server.main.android_blobs.get_cached_path", lambda stem, ext: fake_video)

    resp = client.get(f"/api/android/videos/{VALID_STEM}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"fake-mp4-bytes"


def test_android_video_bytes_returns_404_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(stem, ext):
        raise ResourceNotFoundError("not found")

    monkeypatch.setattr("server.main.android_blobs.get_cached_path", raise_not_found)
    resp = client.get(f"/api/android/videos/{VALID_STEM}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 404


# --- GET /api/android/audio/{stem} ------------------------------------------


def test_android_audio_bytes_requires_a_session() -> None:
    assert client.get(f"/api/android/audio/{VALID_STEM}").status_code == 401


def test_android_audio_bytes_are_served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_audio = tmp_path / f"{VALID_STEM}.wav"
    fake_audio.write_bytes(b"fake-wav-bytes")
    monkeypatch.setattr("server.main.android_blobs.get_cached_path", lambda stem, ext: fake_audio)

    resp = client.get(f"/api/android/audio/{VALID_STEM}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"fake-wav-bytes"


# --- android_blobs.list_shots' place/speed_kmh enrichment (spec 149) -------


def test_list_shots_includes_place_and_speed_from_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from server import android_blobs

    class FakeBlob:
        def __init__(self, name: str, size: int) -> None:
            self.name = name
            self.size = size

    fake_blobs = [
        FakeBlob("android/shot-20260922180735-1.mp4", 500_000),
        FakeBlob("android/shot-20260922180735-1.wav", 200_000),
        FakeBlob("android/shot-20260922180735-1-composed.mp4", 999_999),  # must not become its own row
    ]

    class FakeContainer:
        def list_blobs(self, name_starts_with: str):
            return fake_blobs

    monkeypatch.setattr(android_blobs, "_container", lambda: FakeContainer())
    monkeypatch.setattr(android_blobs, "_read_metadata", lambda stem: {"place": "Sinisestä viivasta päätyyn (22,5 m)", "speedKmh": 86.05})

    shots = android_blobs.list_shots()

    assert len(shots) == 1  # the -composed.mp4 blob is not its own row
    assert shots[0]["stem"] == "shot-20260922180735-1"
    assert shots[0]["place"] == "Sinisestä viivasta päätyyn (22,5 m)"
    assert shots[0]["speed_kmh"] == 86.05


def test_read_metadata_returns_empty_dict_on_any_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from server import android_blobs

    def boom(stem: str, ext: str):
        raise RuntimeError("azure down")

    monkeypatch.setattr(android_blobs, "get_cached_path", boom)
    assert android_blobs._read_metadata(VALID_STEM) == {}


# --- GET /api/android/composed/{stem} (spec 149) ----------------------------


def test_android_composed_bytes_requires_a_session() -> None:
    assert client.get(f"/api/android/composed/{VALID_STEM}").status_code == 401


def test_android_composed_bytes_rejects_an_invalid_stem() -> None:
    resp = client.get("/api/android/composed/not-a-shot", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 404


def test_android_composed_bytes_are_served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_composed = tmp_path / f"{VALID_STEM}-composed.mp4"
    fake_composed.write_bytes(b"fake-composed-bytes")
    monkeypatch.setattr("server.main.android_compose.compose_video", lambda stem: fake_composed)

    resp = client.get(f"/api/android/composed/{VALID_STEM}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"fake-composed-bytes"


def test_android_composed_bytes_returns_503_on_render_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(stem: str):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr("server.main.android_compose.compose_video", boom)
    resp = client.get(f"/api/android/composed/{VALID_STEM}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 503


# --- GET /api/android/composed-meta/{stem} (spec 149) ----------------------


def test_android_composed_meta_requires_a_session() -> None:
    assert client.get(f"/api/android/composed-meta/{VALID_STEM}").status_code == 401


def test_android_composed_meta_returns_duration_and_fps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_composed = tmp_path / f"{VALID_STEM}-composed.mp4"
    fake_composed.write_bytes(b"fake-composed-bytes")
    monkeypatch.setattr("server.main.android_compose.compose_video", lambda stem: fake_composed)
    monkeypatch.setattr("server.main.android_compose.probe_duration_s", lambda path: 2.52)
    monkeypatch.setattr("server.main.android_compose.probe_fps", lambda path: 240.0)
    monkeypatch.setattr("server.main.android_compose.probe_frame_count", lambda path: 605)

    resp = client.get(f"/api/android/composed-meta/{VALID_STEM}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.json() == {"duration_s": 2.52, "fps": 240.0, "frame_count": 605}


# --- GET /api/android/thumbnail/{stem} (spec 149) ---------------------------


def test_android_thumbnail_bytes_requires_a_session() -> None:
    assert client.get(f"/api/android/thumbnail/{VALID_STEM}").status_code == 401


def test_android_thumbnail_bytes_are_served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_thumb = tmp_path / f"{VALID_STEM}-thumb.jpg"
    fake_thumb.write_bytes(b"fake-jpeg-bytes")
    monkeypatch.setattr("server.main.android_compose.thumbnail", lambda stem: fake_thumb)

    resp = client.get(f"/api/android/thumbnail/{VALID_STEM}", cookies={SESSION_COOKIE_NAME: _session_cookie()})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"fake-jpeg-bytes"
