"""Uploads the built Android APK to Azure Blob Storage, where the gallery
server serves it at https://snapshot.timolehtonen.tech/app.apk (spec 136).

    python tools/publish_apk.py                      # the debug build
    python tools/publish_apk.py path/to/other.apk

Same auth as core/azure_sync.py: `az login` + DefaultAzureCredential (needs
Storage Blob Data Contributor on the account). The blob is overwritten in
place; the server never caches it, so the new build is live at once and no
redeploy of the server is needed for a new APK (only for server code).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

STORAGE_ACCOUNT = "shotimprovement"
CONTAINER = "clips"
APK_BLOB_NAME = "downloads/shot-improvement.apk"  # keep in step with server/blob_videos.py
DEFAULT_APK = Path(__file__).resolve().parent.parent / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", nargs="?", type=Path, default=DEFAULT_APK)
    args = parser.parse_args()

    if not args.apk.is_file():
        print(f"APK not found: {args.apk} (build it with android/gradlew.bat :app:assembleDebug)", file=sys.stderr)
        return 1

    service = BlobServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )
    blob = service.get_container_client(CONTAINER).get_blob_client(APK_BLOB_NAME)
    with open(args.apk, "rb") as f:
        blob.upload_blob(
            f,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/vnd.android.package-archive"),
        )
    print(f"Uploaded {args.apk.name} ({args.apk.stat().st_size / 1e6:.1f} MB) -> {APK_BLOB_NAME}")
    print("Live at https://snapshot.timolehtonen.tech/app.apk (install page: /android)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
