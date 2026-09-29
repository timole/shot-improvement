"""Persistent gallery manifest; optimistic writes prevent concurrent upload loss."""
from __future__ import annotations

import json
import threading
import time
import re
import uuid
import logging

from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError, ResourceModifiedError
from azure.storage.blob import ContentSettings

from . import android_blobs

NAME = "android/index.json"
_lock = threading.Lock()
_cached = None
_expires = 0.0
_refreshing = False


def _refresh():
    global _cached, _expires, _refreshing
    try:
        rows, _ = _read()
        with _lock:
            _cached = sorted(rows, key=lambda row: row["stem"], reverse=True)
            _expires = time.monotonic() + 15
    except Exception:
        logging.getLogger(__name__).warning("Gallery refresh failed; serving previous index", exc_info=True)
        with _lock:
            _expires = time.monotonic() + 5
    finally:
        with _lock:
            _refreshing = False


def _read():
    download = android_blobs._container().get_blob_client(NAME).download_blob()
    return json.loads(download.readall()), download.properties.etag


def list_shots():
    global _cached, _expires, _refreshing
    with _lock:
        if _cached is not None:
            if time.monotonic() >= _expires and not _refreshing:
                _refreshing = True
                threading.Thread(target=_refresh, daemon=True, name="gallery-refresh").start()
            return _cached
        try:
            rows, _ = _read()
        except ResourceNotFoundError:
            rows = android_blobs.list_shots()
        _cached = sorted(rows, key=lambda row: row["stem"], reverse=True)
        _expires = time.monotonic() + 15
        return _cached


def update(rows):
    """Merge fields by stem, retrying if another publisher wins the ETag race."""
    global _expires
    blob = android_blobs._container().get_blob_client(NAME)
    for attempt in range(8):
        try:
            current, etag = _read()
        except ResourceNotFoundError:
            current, etag = android_blobs.list_shots(), None
        merged = {row["stem"]: row for row in current}
        for row in rows:
            merged.setdefault(row["stem"], {}).update(row)
        payload = json.dumps(sorted(merged.values(), key=lambda row: row["stem"], reverse=True), ensure_ascii=False)
        try:
            kwargs = {"etag": etag, "match_condition": MatchConditions.IfNotModified} if etag else {}
            blob.upload_blob(payload.encode("utf-8"), overwrite=etag is not None,
                             content_settings=ContentSettings(content_type="application/json"), **kwargs)
            _expires = 0
            return
        except (ResourceExistsError, ResourceModifiedError):
            if attempt == 7:
                raise


def find(stem):
    return next((row for row in list_shots() if row["stem"] == stem), None)


def artifact(stem, extension):
    """Versioned names avoid serving stale annotations after reprocessing."""
    row = find(stem)
    version = (row.get("analysis_version") or "") if row else ""
    if not re.fullmatch(r"[a-f0-9]{16}", version) or extension not in ("mp4", "jpg"):
        return None
    name = f"{stem}-{version}.{extension}"
    path = android_blobs._CACHE_DIR / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.part")
        try:
            with tmp.open("wb") as stream:
                android_blobs._container().get_blob_client(f"android/processed/{name}").download_blob().readinto(stream)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    return path
