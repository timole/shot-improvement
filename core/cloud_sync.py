"""Multi-cloud sync for the shot-improvement web galleries (spec 087,
extended specs 094/095). This app uploads each new clip - both the
raw, native-resolution capture and its palm-box+spectrogram annotated
pair, plus a JPEG preview per video - right after it's recorded, and
deletes the cloud copy when a clip is deleted locally, keeping every
configured storage backend in sync with this machine's own recordings.

Spec 095: this module is now backend-agnostic - `Backend` is a small
structural protocol (`list_remote_names`/`upload_video`/
`upload_preview`/`delete`) that `GcsBackend` (below) implements against
the private `wide-exchanger-463707-c6-shot-improvement` GCS bucket, and
`core.azure_sync.AzureBlobBackend` implements the same way against
Azure Blob Storage - the new Azure-hosted gallery's data store. A
caller (gui.py) passes whichever backends it wants active to
`SyncWorker`; everything else in this file - the queue, the
background thread, the tombstone file, `plan_sync()`, preview
generation - is shared, unchanged in spirit from the single-backend
version, and runs once per backend rather than once total.

Delete-sync is driven by recorded intent (a small local tombstone
file), never by "absent locally therefore delete remotely": a name
present remotely but missing from a given local snapshot isn't
necessarily deleted - it could be mid-upload, or the snapshot could be
racing a rename/restore. Only a name this app was explicitly told to
delete is ever removed from any backend, and it stays a tombstone
until it's confirmed gone from EVERY configured backend - not just the
first one that happens to succeed - surviving both a crash between
"user clicked delete" and "delete succeeded everywhere", and a single
backend being transiently unreachable while the others work fine.

One background thread drains a queue of sync actions (reconcile,
upload-one, delete-one) so no backend's client is ever touched from
two threads at once, and actions can't interleave - same "background
thread + dispatch callback" shape as core.session.LiveSession's encode
worker."""

from __future__ import annotations

import json
import queue
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from .log_setup import get_logger
from .recorder import RECORDINGS_DIR, lower_current_thread_priority
from .video_preview import generate_preview, preview_name

logger = get_logger("cloud_sync")

BUCKET = "wide-exchanger-463707-c6-shot-improvement"
# Spec 094: both members of a recording sync now - the plain
# "shot-improvement-<timestamp>.mp4" raw clip and its "...-annotated.mp4"
# pair, so one glob covering both is what _local_video_names() actually
# needs. ANNOTATED_GLOB is kept alongside it as the narrower, still-
# meaningful "annotated clips only" pattern (nothing in this module
# currently needs that distinction, but it documents the naming
# convention plainly enough to be worth keeping).
ALL_VIDEOS_GLOB = "shot-improvement-*.mp4"
ANNOTATED_GLOB = "*-annotated.mp4"

# GCS resumable-upload chunks must be a multiple of 256 KiB; this is
# also the minimum, chosen deliberately so a typical clip (a few
# hundred KB to a couple MB, per core.recorder) uploads in several
# chunks rather than one - the point of chunking here is on_progress
# granularity (spec 090), not upload efficiency for its own sake.
UPLOAD_CHUNK_SIZE = 256 * 1024

# Beside recordings/, not inside it - RECORDINGS_DIR is globbed for
# annotated clips, and this is neither a clip nor a thing any gallery
# should ever list.
TOMBSTONES_PATH = RECORDINGS_DIR.parent / "cloud_sync_tombstones.json"


class Backend(Protocol):
    """Structural contract every storage backend implements - GcsBackend
    below, core.azure_sync.AzureBlobBackend the same shape. `name` is
    used only for logging. Every method operates on VIDEO names/paths;
    each backend owns its own preview naming/storage internally
    (`upload_preview`/`delete` both take care of the paired preview
    themselves, so callers never need to know previews exist)."""

    name: str

    def list_remote_names(self) -> set[str]: ...
    def upload_video(self, path: Path, on_progress: Optional[Callable[[int, int], None]]) -> None: ...
    def upload_preview(self, jpg_path: Path, blob_name: str) -> None: ...
    def delete(self, name: str) -> None: ...


class GcsBackend:
    """The original backend (spec 087/090/094), now behind the Backend
    protocol. Lazy client, same pattern as before - the app boots
    without credentials, and errors only surface on first use."""

    name = "gcs"

    def __init__(self, bucket_name: str = BUCKET) -> None:
        self._bucket_name = bucket_name
        self._client = None

    def _client_obj(self):
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client()
        return self._client

    def _bucket(self):
        return self._client_obj().bucket(self._bucket_name)

    def list_remote_names(self) -> set[str]:
        # Filtered to videos only - the bucket also holds each video's
        # preview .jpg (spec 094), which must never be mistaken for a
        # video a reconcile pass itself needs to upload/delete.
        return {blob.name for blob in self._bucket().list_blobs() if blob.name.endswith(".mp4")}

    def upload_video(self, path: Path, on_progress: Optional[Callable[[int, int], None]]) -> None:
        blob = self._bucket().blob(path.name)
        if on_progress is None:
            blob.upload_from_filename(str(path), content_type="video/mp4")
            return
        total = path.stat().st_size
        on_progress(0, total)
        with open(path, "rb") as stream:
            # Private API (no public equivalent exposes a resumable
            # upload's own in-progress ResumableUpload/transport pair) -
            # this is exactly what Blob.upload_from_filename does
            # internally; reimplementing the loop ourselves is the only
            # way to observe bytes_uploaded between chunks.
            resumable_upload, transport = blob._initiate_resumable_upload(
                client=self._client_obj(),
                stream=stream,
                content_type="video/mp4",
                size=total,
                num_retries=None,
                chunk_size=UPLOAD_CHUNK_SIZE,
            )
            while not resumable_upload.finished:
                resumable_upload.transmit_next_chunk(transport)
                on_progress(resumable_upload.bytes_uploaded, total)

    def upload_preview(self, jpg_path: Path, blob_name: str) -> None:
        self._bucket().blob(blob_name).upload_from_filename(str(jpg_path), content_type="image/jpeg")

    def delete(self, name: str) -> None:
        self._bucket().blob(name).delete()
        try:
            self._bucket().blob(preview_name(name)).delete()
        except Exception:
            # Not found is expected/benign if generation failed
            # originally, or already deleted - don't let this break the
            # video delete's own success signal.
            logger.debug("GcsBackend.delete: preview for %s not found or already gone", name, exc_info=True)


def plan_sync(local_names: set[str], remote_names: set[str], tombstones: set[str]) -> tuple[set[str], set[str]]:
    """Pure. Given the video file names present locally (raw and
    annotated alike, spec 094), the video object names present on ONE
    backend, and this machine's own delete-intent record, returns
    (to_upload, to_delete) for THAT backend. Called once per configured
    backend (spec 095) - a name present on one backend but not another
    (e.g. mid-migration to a newly added backend) is corrrectly
    uploaded to the one missing it, not skipped because some OTHER
    backend already has it. A name is deleted remotely only because
    it's in `tombstones` - never merely because it's absent from
    `local_names`, which can't tell a genuine delete apart from a
    not-yet-finished upload or a moved/half-restored folder."""
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


def _local_video_names(recordings_dir: Path = RECORDINGS_DIR) -> set[str]:
    """Every clip that should be in the cloud (spec 094): both the raw,
    native-resolution capture and its annotated pair - previously only
    ANNOTATED_GLOB synced, leaving every raw clip laptop-only."""
    return {p.name for p in recordings_dir.glob(ALL_VIDEOS_GLOB)}


def upload(path: Path, backends: Sequence[Backend], on_progress: Optional[Callable[[int, int], None]] = None) -> None:
    """Uploads `path` (one video file) to every given backend, plus a
    preview JPEG generated ONCE (spec 094/095 - not once per backend,
    which would shell out to ffmpeg redundantly) and handed to each
    backend's own upload_preview().

    on_progress(done, total), if given, reports COMBINED progress
    scaled across every backend's video upload (spec 090 x spec 095) -
    `total` is `len(backends) * file_size`, so the bar still moves
    smoothly through 0%-100% across all of them rather than resetting
    per backend or only reflecting the first one's work.

    Resilient across backends: one backend failing doesn't stop the
    others from being attempted (so e.g. a transient Azure outage
    never blocks a GCS upload, keeping whichever gallery IS reachable
    current) - but if any backend failed, the FIRST such exception is
    still raised at the end, so callers (SyncWorker) see this action as
    failed and don't discard its tombstone / mark it done."""
    total = path.stat().st_size
    backend_count = len(backends)
    if on_progress is not None:
        on_progress(0, backend_count * total)

    with tempfile.TemporaryDirectory() as tmp:
        preview_path = Path(tmp) / preview_name(path.name)
        preview_ok = generate_preview(path, preview_path)

        first_exc: Optional[Exception] = None
        for i, backend in enumerate(backends):
            offset = i * total

            def scaled_progress(done: int, _total: int, offset: int = offset) -> None:
                on_progress(offset + done, backend_count * total)  # type: ignore[misc]

            try:
                backend.upload_video(path, scaled_progress if on_progress is not None else None)
                logger.info("upload: %s -> %s", path.name, backend.name)
            except Exception as e:
                logger.exception("upload: backend %r failed for %s", backend.name, path.name)
                if first_exc is None:
                    first_exc = e
                continue  # still try the other backends
            if preview_ok:
                try:
                    backend.upload_preview(preview_path, preview_name(path.name))
                    logger.info("upload preview: %s -> %s", preview_name(path.name), backend.name)
                except Exception:
                    # Best-effort, same as the single-backend original -
                    # a missing thumbnail is a degraded gallery entry,
                    # never a sync failure.
                    logger.warning("upload: preview upload failed on backend %r for %s", backend.name, path.name, exc_info=True)

    if first_exc is not None:
        raise first_exc


def delete(name: str, backends: Sequence[Backend]) -> None:
    """Deletes `name` from every given backend. Resilient the same way
    upload() is: every backend gets an attempt regardless of an
    earlier one failing, but the first failure is still raised at the
    end so the caller knows this action wasn't fully resolved (and,
    critically, so SyncWorker doesn't discard the tombstone for a name
    that's still live on a backend that failed)."""
    first_exc: Optional[Exception] = None
    for backend in backends:
        try:
            backend.delete(name)
            logger.info("delete: %s -> %s", name, backend.name)
        except Exception as e:
            logger.exception("delete: backend %r failed for %s", backend.name, name)
            if first_exc is None:
                first_exc = e
    if first_exc is not None:
        raise first_exc


class SyncWorker:
    """Runs every sync action (startup reconcile, per-recording upload,
    per-delete removal) on one dedicated background thread, serialized
    through a queue - so no backend's client is ever touched from two
    threads at once, and a reconcile can never interleave with (and
    e.g. mistake for a remote orphan) a just-finished recording's own
    upload.

    `backends` (spec 095): the storage backends to keep in sync - e.g.
    `[GcsBackend(), azure_sync.AzureBlobBackend()]`. Every action runs
    against all of them.

    `dispatch`, same convention as LiveSession.start_recording's
    argument, runs each action's `on_done` callback back on whatever
    thread constructed this (e.g. Tkinter's root.after(0, fn)) -
    `on_done` must never touch GUI widgets directly otherwise."""

    def __init__(
        self,
        backends: Sequence[Backend],
        dispatch: Callable[[Callable[[], None]], None] = lambda fn: fn(),
    ) -> None:
        self._backends = list(backends)
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
        function's own docstring for how it's scaled across backends
        (spec 095)."""
        self._queue.put(("upload", path, on_done, on_progress))

    def delete_recording(self, name: str, on_done: Optional[Callable[[Optional[Exception]], None]] = None) -> None:
        # Recorded before the action even reaches the queue - see the
        # module docstring on why a tombstone, once written, must
        # outlive a crash.
        self._tombstones.add(name)
        save_tombstones(self._tombstones)
        self._queue.put(("delete", name, on_done, None))

    def _run(self) -> None:
        # Spec 091: same reasoning as core.session's encode worker - a
        # recording's live capture should get CPU/network scheduling
        # preference over this background sync work, not compete evenly.
        lower_current_thread_priority()
        while True:
            action, arg, on_done, on_progress = self._queue.get()
            exc: Optional[Exception] = None
            try:
                if action == "reconcile":
                    self._reconcile()
                elif action == "upload":
                    if on_progress is None:
                        upload(arg, self._backends)  # type: ignore[arg-type]
                    else:
                        upload(arg, self._backends, on_progress=lambda done, total: self._dispatch(lambda: on_progress(done, total)))  # type: ignore[arg-type,misc]
                elif action == "delete":
                    delete(arg, self._backends)  # type: ignore[arg-type]
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
        # RECORDINGS_DIR passed explicitly (not relying on
        # _local_video_names' own default parameter) - a default value
        # is bound once at function-definition time, so a caller that
        # monkeypatches the module-level RECORDINGS_DIR name later (as
        # tests do) would otherwise silently see the ORIGINAL value
        # here regardless.
        local_names = _local_video_names(RECORDINGS_DIR)
        remote_by_backend = [(backend, backend.list_remote_names()) for backend in self._backends]

        uploaded = 0
        for backend, remote_names in remote_by_backend:
            to_upload, _ = plan_sync(local_names, remote_names, self._tombstones)
            for name in to_upload:
                # One backend at a time here (not the whole self._backends
                # list) - each backend's own missing set can differ (e.g.
                # mid-migration to a newly added backend), so this only
                # uploads to the backend that's actually missing it. Costs
                # a redundant preview regeneration if the SAME name is
                # missing from more than one backend in the same pass -
                # acceptable: reconcile is rare (startup, or catching up a
                # newly added backend), unlike the per-recording upload
                # path above, which already generates the preview once for
                # every backend in a single call.
                upload(RECORDINGS_DIR / name, [backend])
                uploaded += 1

        deleted = 0
        resolved_tombstones: set[str] = set()
        for backend, remote_names in remote_by_backend:
            to_delete = self._tombstones & remote_names
            for name in to_delete:
                delete(name, [backend])
                deleted += 1
            resolved_tombstones |= to_delete

        # A tombstone is only retired once it's been found - and just
        # deleted - on every backend that had it (or the loop above would
        # have raised and none of this would run). A tombstone never seen
        # on ANY backend this pass (already gone everywhere, or never
        # uploaded before being deleted locally) is equally safe to leave
        # exactly as it was - NOT force-cleared, matching the original
        # single-backend behavior of only ever discarding names that were
        # actually found-and-deleted.
        self._tombstones -= resolved_tombstones
        save_tombstones(self._tombstones)
        logger.info("reconcile: uploaded=%d deleted=%d (backends=%s)", uploaded, deleted, [b.name for b, _ in remote_by_backend])
