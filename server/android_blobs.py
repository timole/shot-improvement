"""Azure Blob Storage for the Android app's own shots (spec 147) -
upload (phone -> server -> blob, POST /api/android/upload) and read
(server -> browser, for snapshot.timolehtonen.tech's download page) both
live here, in the SAME "clips" container as the desktop gallery's own
clips but under the "android/" prefix, so the two features can't
collide on a blob name. Deliberately a separate module from
server/blob_videos.py rather than folding into it - that module's own
docstring specifically frames it as this server's read-only side; this
one both reads and writes, for a completely different client (a phone,
not the desktop app's laptop).

Auth for reads is the existing Microsoft-login session cookie (see
server/main.py's _session_email) - same as the desktop gallery. Auth
for uploads is a separate, simple shared-secret Bearer token
(SHOT_ANDROID_UPLOAD_TOKEN) - the phone never does the browser-based
Microsoft OAuth flow, so it can't get a session cookie the normal way.

Credential: DefaultAzureCredential, this Container App's managed
identity - which spec 147 upgraded from "Storage Blob Data Reader" to
"Storage Blob Data Contributor" (see docs/azure.md) specifically so
this module's upload path can write, not just server/blob_videos.py's
read-only one."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

STORAGE_ACCOUNT = os.environ.get("AZURE_STORAGE_ACCOUNT", "shotimprovement")
CONTAINER = os.environ.get("AZURE_BLOB_CONTAINER", "clips")
PREFIX = "android/"

# Matches tech.timolehtonen.shot.ShotFiles.timestamp() ("yyyyMMddHHmmss")
# plus the per-session shot index MainActivity.stemFor builds -
# "dev-shot-..." stems are never uploaded in the first place (spec 147:
# dev-mode shots stay local-only, same as they already never touch
# shots.jsonl - spec 139), so this intentionally doesn't match that
# prefix. Also the gate that keeps every route below from ever reading
# or writing an arbitrary blob name in the container.
STEM_RE = re.compile(r"^shot-(\d{14})-(\d+)$")

_CACHE_DIR = Path(tempfile.gettempdir()) / "shot-improvement-android-cache"
_CACHE_MAX_FILES = 200

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


def is_valid_stem(stem: str) -> bool:
    return bool(STEM_RE.match(stem))


def _recorded_at(stem: str) -> str | None:
    match = STEM_RE.match(stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def upload_shot(stem: str, video: bytes | None, audio: bytes | None, metadata: bytes | None) -> None:
    """Writes whichever of the three parts the phone actually sent (spec
    140: a shot with no camera available that session has no video) under
    "android/<stem>.{mp4,wav,json}". Overwrite is fine - a stem is only
    ever uploaded once in normal use, but a retried upload after a
    dropped connection must not fail as "already exists"."""
    if not is_valid_stem(stem):
        raise ValueError(f"not a valid shot stem: {stem!r}")
    container = _container()
    if video is not None:
        container.upload_blob(
            name=f"{PREFIX}{stem}.mp4", data=video, overwrite=True,
            content_settings=ContentSettings(content_type="video/mp4"),
        )
    if audio is not None:
        container.upload_blob(
            name=f"{PREFIX}{stem}.wav", data=audio, overwrite=True,
            content_settings=ContentSettings(content_type="audio/wav"),
        )
    if metadata is not None:
        container.upload_blob(
            name=f"{PREFIX}{stem}.json", data=metadata, overwrite=True,
            content_settings=ContentSettings(content_type="application/json", cache_control="no-store"),
        )


def _read_metadata(stem: str) -> dict:
    """The phone's own metadata.json for stem (place, speedKmh, ...) -
    see android/app/.../MainActivity.kt's upload metadata shape.
    Cached the same way get_cached_path caches video/audio (the file
    never changes once uploaded); {} if there is none, or it fails to
    parse, since list_shots must never break over one bad shot."""
    try:
        path = get_cached_path(stem, "json")
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_shots() -> list[dict]:
    """[{"stem", "recorded_at", "has_video", "has_audio", "video_size",
    "place", "speed_kmh"}], newest first - one row per *stem*, not per
    blob, so a shot with only audio (camera unavailable that session)
    still gets exactly one row instead of a missing-video gap in a
    per-blob list. place/speed_kmh come from the phone's own metadata
    sidecar (spec 149) - the same fields MainActivity's own Historia
    list shows, so the web gallery's list can match it."""
    by_stem: dict[str, dict] = {}
    for blob in _container().list_blobs(name_starts_with=PREFIX):
        name = blob.name[len(PREFIX):]
        if name.endswith(".mp4") and not name.endswith("-composed.mp4"):
            stem, kind, size = name[: -len(".mp4")], "video", blob.size
        elif name.endswith(".wav"):
            stem, kind, size = name[: -len(".wav")], "audio", blob.size
        else:
            continue
        if not is_valid_stem(stem):
            continue
        row = by_stem.setdefault(
            stem, {"stem": stem, "recorded_at": _recorded_at(stem), "has_video": False, "has_audio": False, "video_size": None},
        )
        if kind == "video":
            row["has_video"] = True
            row["video_size"] = size
        else:
            row["has_audio"] = True
    shots = list(by_stem.values())
    for row in shots:
        meta = _read_metadata(row["stem"])
        row["place"] = meta.get("place")
        row["speed_kmh"] = meta.get("speedKmh")
    shots.sort(key=lambda s: s["stem"], reverse=True)
    return shots


def get_cached_path(stem: str, extension: str) -> Path:
    """Downloads "android/<stem>.<extension>" into a small per-instance
    cache first on a miss - same shape as server/blob_videos.py's own
    get_cached_path (kept as a separate copy, see this module's own
    docstring for why, rather than sharing code across the two)."""
    if not is_valid_stem(stem) or extension not in ("mp4", "wav", "json"):
        raise ValueError(f"not a valid android file: {stem!r}.{extension}")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{stem}.{extension}"
    path = _CACHE_DIR / filename
    if path.exists():
        return path
    tmp_path = path.with_name(path.name + ".part")
    with open(tmp_path, "wb") as f:
        _container().get_blob_client(f"{PREFIX}{filename}").download_blob().readinto(f)
    tmp_path.replace(path)
    _evict_oldest()
    return path


def _evict_oldest() -> None:
    cached = sorted(_CACHE_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in cached[_CACHE_MAX_FILES:]:
        stale.unlink(missing_ok=True)
