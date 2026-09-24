"""Live "what is the laptop doing right now" status for the
snapshot.timolehtonen.tech page (spec 120).

A tiny JSON blob (`status/latest.json`) in the same Azure container as
the clips, rewritten at each stage of a recording: capture ended ->
fastest shot known -> raw clip uploaded -> annotated clip uploaded. The
page polls it (via the server's /api/status) to show a countdown and the
newest clip without a reload.

Publishing never blocks or fails the caller: updates go through a
single background thread, only the newest pending state is sent (a slow
mobile network must not queue up stale states), and errors are logged.

Times are UTC ISO strings; the page compares them against the
server's own clock (returned alongside the status), so laptop/browser
clock skew doesn't distort the countdown.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from .azure_sync import AzureBlobBackend
from .log_setup import get_logger

logger = get_logger("status_publish")

STATUS_BLOB_NAME = "status/latest.json"
# Measured wall-clock seconds from "capture ended" to each clip being
# uploaded, on this laptop over a normal connection (spec 120's
# measurement: raw 34s, annotated 51s). Only estimates for the page's
# countdown - the real "ready" transitions replace them.
RAW_ETA_S = 34
ANNOTATED_ETA_S = 51


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StatusPublisher:
    def __init__(self, backend: Optional[AzureBlobBackend] = None) -> None:
        self._backend = backend or AzureBlobBackend()
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}
        self._dirty = threading.Event()
        threading.Thread(target=self._run, name="shot-improvement-status", daemon=True).start()

    def begin(self, rec_id: str, deferred: bool = False) -> None:
        """Capture just ended for recording `rec_id` (YYYYmmddHHMMSS).
        deferred ("Vain nauhoitus"): no clips will be produced until the
        recording is processed later, so the page gets state "deferred"
        (fastest shot only, no countdown)."""
        with self._lock:
            self._state = {
                "id": rec_id,
                "state": "deferred" if deferred else "processing",
                "capture_ended_at": _now_iso(),
                "raw_eta_s": RAW_ETA_S,
                "annotated_eta_s": ANNOTATED_ETA_S,
                "fastest_kmh": None,
            }
        self._dirty.set()

    def update(self, rec_id: str, **fields: Any) -> None:
        """No-op if `rec_id` isn't the recording currently tracked (a
        stale callback from an older recording must not overwrite it)."""
        with self._lock:
            if self._state.get("id") != rec_id:
                return
            self._state.update(fields)
            if fields.get("state") == "raw_ready":
                self._state["raw_ready_at"] = _now_iso()
            elif fields.get("state") == "done":
                self._state["done_at"] = _now_iso()
        self._dirty.set()

    def _run(self) -> None:
        while True:
            self._dirty.wait()
            self._dirty.clear()
            with self._lock:
                payload = json.dumps(self._state).encode("utf-8")
            try:
                self._backend.upload_bytes(STATUS_BLOB_NAME, payload, "application/json")
            except Exception:
                logger.warning("StatusPublisher: upload failed", exc_info=True)
