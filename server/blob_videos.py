"""Azure Blob Storage-backed video listing/reading for the
snapshot.timolehtonen.tech gallery (spec 095) - this server's read side of
what core/azure_sync.py writes from the laptop.

Deliberately does NOT import anything from core/ - core/recorder.py
(and everything that imports it) pulls in opencv-python and mediapipe,
which this container must never need (a gallery server has no
business importing a camera/pose-detection stack). The small bits
actually needed here (the filename pattern, the preview-name mapping)
are duplicated rather than shared - see core/cloud_sync.py and
core/video_preview.py for the laptop app's own copies.

Auth: DefaultAzureCredential, resolving to this Container App's
system-assigned managed identity at runtime (Storage Blob Data Reader
role - read-only, since only the laptop ever writes) - no secret at
all, same as core/azure_sync.py's laptop-side az-login path."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = os.environ.get("AZURE_STORAGE_ACCOUNT", "shotimprovement")
CONTAINER = os.environ.get("AZURE_BLOB_CONTAINER", "clips")

# Matches exactly what core/cloud_sync.py uploads - v1 lists ANNOTATED
# clips only (spec 095: matching what the existing ai.timolehtonen.tech
# gallery shows today - the raw clip is in the same container, for a
# later version, not surfaced here). Also the gate that keeps
# GET /api/videos/{name} from ever reading an arbitrary blob name out
# of the container.
VIDEO_NAME_RE = re.compile(r"^shot-improvement-(\d{14})-annotated\.mp4$")
# Spec 120: the raw clip is viewable too (the page shows it as soon as
# it's uploaded, before the annotated one is ready) - servable by name,
# but still not listed in list_videos().
RAW_VIDEO_NAME_RE = re.compile(r"^shot-improvement-(\d{14})\.mp4$")
STATUS_BLOB_NAME = "status/latest.json"
# Spec 136: the Android app's APK, uploaded by tools/publish_apk.py. Same
# container, so the Container App's existing Blob Data Reader role covers it.
APK_BLOB_NAME = "downloads/shot-improvement.apk"
PREVIEW_NAME_RE = re.compile(r"^shot-improvement-(\d{14})-annotated\.jpg$")

# Container Apps' ephemeral storage is real disk (unlike Cloud Run's
# RAM-backed /tmp), shared with the rest of the container filesystem -
# comfortably enough at this app's size for these caps; see specs/095.
_CACHE_DIR = Path(tempfile.gettempdir()) / "shot-improvement-cache"
_CACHE_MAX_VIDEOS = 10
_CACHE_MAX_PREVIEWS = 200

_service_client = None


def _service() -> BlobServiceClient:
    global _service_client
    if _service_client is None:
        _service_client = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
    return _service_client


def _container():
    return _service().get_container_client(CONTAINER)


def is_valid_video_name(name: str) -> bool:
    return bool(VIDEO_NAME_RE.match(name) or RAW_VIDEO_NAME_RE.match(name))


def is_valid_preview_name(name: str) -> bool:
    return bool(PREVIEW_NAME_RE.match(name))


def _recorded_at(name: str) -> str | None:
    match = VIDEO_NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def list_videos() -> list[dict]:
    """Returns [{"name", "size", "recorded_at"}], newest first. Objects
    that don't match VIDEO_NAME_RE (raw clips, previews - the container
    holds both, only annotated clips list here) are silently skipped
    rather than surfaced as broken rows."""
    blobs = _container().list_blobs()
    videos = [
        {"name": blob.name, "size": blob.size, "recorded_at": _recorded_at(blob.name)}
        for blob in blobs
        if is_valid_video_name(blob.name)
    ]
    videos.sort(key=lambda v: v["name"], reverse=True)
    return videos


def get_cached_path(name: str) -> Path:
    """Returns a local path holding `name`'s bytes (a video OR a
    preview image), downloading into a small per-instance cache first
    on a miss. Filenames embed their timestamp and are never rewritten
    once uploaded, so a cache hit is always valid - there's no
    freshness check to make. Raises
    azure.core.exceptions.ResourceNotFoundError if the blob doesn't
    exist.

    Downloads to a temporary, uuid-suffixed tmp path and atomically
    renames it into place, rather than writing straight to the final
    cache path - a second concurrent request (a video player firing
    several parallel Range requests at once against a cold cache hits
    this routinely) must never be able to serve a half-written file.
    The tmp path is unique per call, not just '<name>.part' - two
    concurrent misses used to be able to open() the SAME tmp path and
    interleave their writes into it (whichever finished first would
    rename the corrupt result into place; the other would then fail to
    rename its own now-vanished tmp path, an exception easy for a
    caller to swallow as "this video doesn't exist"). A unique tmp path
    per call means concurrent downloads of the same file are merely
    redundant, never corrupting."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / name
    if path.exists():
        return path
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    with open(tmp_path, "wb") as f:
        _container().get_blob_client(name).download_blob().readinto(f)
    tmp_path.replace(path)
    _evict_oldest()
    return path


def _evict_oldest() -> None:
    # Concurrent cache misses call this at the same time (see
    # android_blobs._evict_oldest's own docstring for the same
    # reasoning) - a file glob() sees can vanish (renamed/unlinked by
    # another concurrent call) before stat() gets to it, raising
    # FileNotFoundError. These patterns already exclude the ".part"
    # tmp download path by construction (it never ends in ".mp4" or
    # ".jpg"), but two evictions can still race each other, so stat()
    # failures are tolerated the same way.
    for pattern, keep in (("*.mp4", _CACHE_MAX_VIDEOS), ("*.jpg", _CACHE_MAX_PREVIEWS)):
        entries = []
        for p in _CACHE_DIR.glob(pattern):
            try:
                entries.append((p.stat().st_mtime, p))
            except OSError:
                continue
        entries.sort(key=lambda item: item[0], reverse=True)
        cached = [p for _, p in entries]
        for stale in cached[keep:]:
            stale.unlink(missing_ok=True)


def read_apk() -> bytes:
    """The current Android APK's bytes, fetched fresh every time - unlike
    get_cached_path's clips, this blob is overwritten on each release, so
    a cache keyed by name would serve a stale build. Raises
    azure.core.exceptions.ResourceNotFoundError if none is published."""
    return _container().get_blob_client(APK_BLOB_NAME).download_blob().readall()


def read_status() -> dict | None:
    """The laptop's live status blob (core/status_publish.py), or None if
    it hasn't been written yet / can't be parsed."""
    import json

    from azure.core.exceptions import ResourceNotFoundError

    try:
        data = _container().get_blob_client(STATUS_BLOB_NAME).download_blob().readall()
    except ResourceNotFoundError:
        return None
    try:
        status = json.loads(data)
    except ValueError:
        return None
    return status if isinstance(status, dict) else None
