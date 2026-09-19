"""Azure Blob Storage backend for core.cloud_sync (spec 095) - the data
store behind the new Azure-hosted gallery at shot.timolehtonen.tech.
Implements core.cloud_sync.Backend the same shape GcsBackend does, so
SyncWorker can drive both at once (dual-write during the transition -
see specs 094/095's "why").

Auth: `DefaultAzureCredential` (package azure-identity), which on this
laptop picks up an `az login` session automatically - same UX as GCS's
`gcloud auth application-default login`, no long-lived secret to store
locally. Needs the "Storage Blob Data Contributor" role granted to the
signed-in account on the storage account (see specs/095's runbook) -
without it every call 403s with AuthorizationPermissionMismatch, which
is the classic first-time-Azure trap: subscription Owner/Contributor
grants nothing at the DATA plane."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .log_setup import get_logger
from .video_preview import preview_name

logger = get_logger("azure_sync")

# Chosen at provisioning time - see specs/095's runbook for the exact
# `az storage account create`/`az storage container-rm create` this
# must match.
STORAGE_ACCOUNT = "shotimprovement"
CONTAINER = "clips"

# Forces azure-storage-blob's upload_blob() onto its chunked code path
# (spec 090 parity) - its own default (64 MiB) means a clip a few
# hundred KB to a couple MB would go up as ONE unchunked PUT, firing
# progress_hook once (0% -> 100% jump) instead of smoothly. Matches
# core.cloud_sync.UPLOAD_CHUNK_SIZE's reasoning exactly, same value.
UPLOAD_CHUNK_SIZE = 256 * 1024


class AzureBlobBackend:
    name = "azure"

    def __init__(self, account: str = STORAGE_ACCOUNT, container: str = CONTAINER) -> None:
        self._account = account
        self._container_name = container
        self._service_client = None

    def _service(self):
        if self._service_client is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            self._service_client = BlobServiceClient(
                account_url=f"https://{self._account}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
                max_single_put_size=UPLOAD_CHUNK_SIZE,
                max_block_size=UPLOAD_CHUNK_SIZE,
            )
        return self._service_client

    def _container(self):
        return self._service().get_container_client(self._container_name)

    def list_remote_names(self) -> set[str]:
        return {blob.name for blob in self._container().list_blobs() if blob.name.endswith(".mp4")}

    def upload_video(self, path: Path, on_progress: Optional[Callable[[int, int], None]]) -> None:
        from azure.storage.blob import ContentSettings

        if on_progress is not None:
            on_progress(0, path.stat().st_size)

        def progress_hook(current: int, total: int) -> None:
            if on_progress is not None:
                on_progress(current, total)

        with open(path, "rb") as stream:
            self._container().upload_blob(
                name=path.name,
                data=stream,
                overwrite=True,
                content_settings=ContentSettings(content_type="video/mp4"),
                progress_hook=progress_hook if on_progress is not None else None,
            )

    def upload_bytes(self, name: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        self._container().upload_blob(
            name=name, data=data, overwrite=True,
            content_settings=ContentSettings(content_type=content_type, cache_control="no-store"),
        )

    def upload_preview(self, jpg_path: Path, blob_name: str) -> None:
        from azure.storage.blob import ContentSettings

        with open(jpg_path, "rb") as stream:
            self._container().upload_blob(
                name=blob_name, data=stream, overwrite=True,
                content_settings=ContentSettings(content_type="image/jpeg"),
            )

    def delete(self, name: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        self._container().delete_blob(name)
        try:
            self._container().delete_blob(preview_name(name))
        except ResourceNotFoundError:
            # Not found is expected/benign if generation failed
            # originally, or already deleted - don't let this break the
            # video delete's own success signal.
            logger.debug("AzureBlobBackend.delete: preview for %s not found or already gone", name)
