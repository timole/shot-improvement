"""GCS sync for the shot-improvement web gallery (spec 087). This app
uploads each new annotated clip right after it's recorded, and deletes
the cloud copy when a clip is deleted locally, keeping the private
`wide-exchanger-463707-c6-shot-improvement` bucket in sync with this
machine's own annotated clips (raw clips never leave the laptop). See
README.md's "Web gallery" section - the server that used to read this
bucket back out to a browser was removed when this app moved into its
own repo; this module doesn't depend on that server and still works
standalone.

Delete-sync is driven by recorded intent (a small local tombstone
file), never by "absent locally therefore delete remotely": a name
present remotely but missing from a given local snapshot isn't
necessarily deleted - it could be mid-upload, or the snapshot could be
racing a rename/restore. Only a name this app was explicitly told to
delete is ever removed from the bucket, and it stays a tombstone until
the remote object is actually gone (surviving a crash between "user
clicked delete" and "GCS delete succeeded").

One background thread drains a queue of sync actions (reconcile,
upload-one, delete-one) so the GCS client is only ever touched from one
thread and actions can't interleave - same "background thread +
dispatch callback" shape as core.session.LiveSession's encode worker."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from .log_setup import get_logger
from .recorder import RECORDINGS_DIR

logger = get_logger("cloud_sync")

BUCKET = "wide-exchanger-463707-c6-shot-improvement"
ANNOTATED_GLOB = "*-annotated.mp4"

# GCS resumable-upload chunks must be a multiple of 256 KiB; this is
# also the minimum, chosen deliberately so a typical clip (a few
# hundred KB to a couple MB, per core.recorder) uploads in several
# chunks rather than one - the point of chunking here is on_progress
# granularity (spec 090), not upload efficiency for its own sake.
UPLOAD_CHUNK_SIZE = 256 * 1024

# Beside recordings/, not inside it - RECORDINGS_DIR is globbed for
# annotated clips, and this is neither a clip nor a thing the gallery
# should ever list.
TOMBSTONES_PATH = RECORDINGS_DIR.parent / "cloud_sync_tombstones.json"

_client = None


def _get_client():
    global _client
    if _client is None:
        from google.cloud import storage

        _client = storage.Client()
    return _client


def plan_sync(local_names: set[str], remote_names: set[str], tombstones: set[str]) -> tuple[set[str], set[str]]:
    """Pure. Given the annotated-file names present locally, the object
    names present remotely, and this machine's own delete-intent
    record, returns (to_upload, to_delete). A name is deleted remotely
    only because it's in `tombstones` - never merely because it's
    absent from `local_names`, which can't tell a genuine delete apart
    from a not-yet-finished upload or a moved/half-restored folder."""
    to_upload = local_names - remote_names - tombstones
    to_delete = tombstones & remote_names
    return to_upload, to_delete


def load_tombstones() -> set[str]:
    if not TOMBSTONES_PATH.exists():
        return set()
    try:
        return set(json.loads(TOMBSTONES_PATH.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        logger.exception("load_tombstones: failed to read %s", TOMBSTONES_PATH)
        return set()


def save_tombstones(tombstones: set[str]) -> None:
    TOMBSTONES_PATH.write_text(json.dumps(sorted(tombstones)), encoding="utf-8")


def list_remote_names() -> set[str]:
    return {blob.name for blob in _get_client().bucket(BUCKET).list_blobs()}


def upload(path: Path, on_progress: Optional[Callable[[int, int], None]] = None) -> None:
    """on_progress(bytes_sent, total_bytes), if given, is called after
    each uploaded chunk (spec 090). Without it, a plain single-call
    upload is used (simpler, and exactly what every non-GUI caller
    needs) - the chunked path below exists only to get progress
    visibility during an upload slow enough to be worth watching."""
    blob = _get_client().bucket(BUCKET).blob(path.name)
    if on_progress is None:
        blob.upload_from_filename(str(path), content_type="video/mp4")
        logger.info("upload: %s", path.name)
        return

    total = path.stat().st_size
    on_progress(0, total)
    with open(path, "rb") as stream:
        # Private API (no public equivalent exposes a resumable
        # upload's own in-progress ResumableUpload/transport pair) -
        # this is exactly what Blob.upload_from_filename does
        # internally; reimplementing the loop ourselves is the only way
        # to observe bytes_uploaded between chunks.
        resumable_upload, transport = blob._initiate_resumable_upload(
            client=_get_client(),
            stream=stream,
            content_type="video/mp4",
            size=total,
            num_retries=None,
            chunk_size=UPLOAD_CHUNK_SIZE,
        )
        while not resumable_upload.finished:
            resumable_upload.transmit_next_chunk(transport)
            on_progress(resumable_upload.bytes_uploaded, total)
    logger.info("upload: %s", path.name)


def delete(name: str) -> None:
    _get_client().bucket(BUCKET).blob(name).delete()
    logger.info("delete: %s", name)


class SyncWorker:
    """Runs every sync action (startup reconcile, per-recording upload,
    per-delete removal) on one dedicated background thread, serialized
    through a queue - so the GCS client is never touched from two
    threads at once, and a reconcile can never interleave with (and
    e.g. mistake for a remote orphan) a just-finished recording's own
    upload.

    `dispatch`, same convention as LiveSession.start_recording's
    argument, runs each action's `on_done` callback back on whatever
    thread constructed this (e.g. Tkinter's root.after(0, fn)) -
    `on_done` must never touch GUI widgets directly otherwise."""

    def __init__(self, dispatch: Callable[[Callable[[], None]], None] = lambda fn: fn()) -> None:
        self._dispatch = dispatch
        self._queue: "queue.Queue[tuple[str, object, Optional[Callable[[Optional[Exception]], None]], Optional[Callable[[int, int], None]]]]" = queue.Queue()
        self._tombstones = load_tombstones()
        threading.Thread(target=self._run, name="shot-improvement-sync", daemon=True).start()

    def start_reconcile(self, on_done: Optional[Callable[[Optional[Exception]], None]] = None) -> None:
        self._queue.put(("reconcile", None, on_done, None))

    def upload_recording(
        self,
        path: Path,
        on_done: Optional[Callable[[Optional[Exception]], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """on_progress(bytes_sent, total_bytes), if given, is dispatched
        (spec 090) as core.cloud_sync.upload() reports it - see that
        function's own docstring."""
        self._queue.put(("upload", path, on_done, on_progress))

    def delete_recording(self, name: str, on_done: Optional[Callable[[Optional[Exception]], None]] = None) -> None:
        # Recorded before the action even reaches the queue - see the
        # module docstring on why a tombstone, once written, must
        # outlive a crash.
        self._tombstones.add(name)
        save_tombstones(self._tombstones)
        self._queue.put(("delete", name, on_done, None))

    def _run(self) -> None:
        while True:
            action, arg, on_done, on_progress = self._queue.get()
            exc: Optional[Exception] = None
            try:
                if action == "reconcile":
                    self._reconcile()
                elif action == "upload":
                    if on_progress is None:
                        upload(arg)  # type: ignore[arg-type]
                    else:
                        upload(arg, on_progress=lambda done, total: self._dispatch(lambda: on_progress(done, total)))  # type: ignore[arg-type,misc]
                elif action == "delete":
                    delete(arg)  # type: ignore[arg-type]
                    self._tombstones.discard(arg)
                    save_tombstones(self._tombstones)
            except Exception as e:
                # Never let this kill the worker thread - the next
                # queued action (or the next reconcile) must still run.
                logger.exception("SyncWorker: %s failed", action)
                exc = e
            if on_done:
                self._dispatch(lambda exc=exc: on_done(exc))

    def _reconcile(self) -> None:
        local_names = {p.name for p in RECORDINGS_DIR.glob(ANNOTATED_GLOB)}
        remote_names = list_remote_names()
        to_upload, to_delete = plan_sync(local_names, remote_names, self._tombstones)
        for name in to_upload:
            upload(RECORDINGS_DIR / name)
        for name in to_delete:
            delete(name)
            self._tombstones.discard(name)
        save_tombstones(self._tombstones)
        logger.info("reconcile: uploaded=%d deleted=%d", len(to_upload), len(to_delete))
