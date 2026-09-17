"""One-off maintenance tool (spec 094): generates and uploads a preview
JPEG for every video ALREADY in the bucket that doesn't have one yet.

Normal sync (core.cloud_sync.upload(), called by every new
upload/reconcile) generates a preview as a side effect of uploading a
video - but that only covers videos uploaded from here on. Any video
already in the bucket before spec 094 predates that logic entirely and
has no preview; this closes that one-time gap. Safe to re-run anytime
(only touches videos still missing a preview).

Usage:
    venv\\Scripts\\python tools\\backfill_previews.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cloud_sync import BUCKET, _generate_preview, _get_client, _preview_name
from core.log_setup import get_logger, setup_logging

setup_logging()
logger = get_logger("backfill_previews")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    bucket = _get_client().bucket(BUCKET)
    blobs = list(bucket.list_blobs())
    video_names = {b.name for b in blobs if b.name.endswith(".mp4")}
    existing_previews = {b.name for b in blobs if b.name.endswith(".jpg")}
    missing = sorted(name for name in video_names if _preview_name(name) not in existing_previews)

    print(f"{len(video_names)} videos in bucket, {len(existing_previews)} already have previews.")
    if not missing:
        print("Nothing to backfill.")
        return
    print(f"Backfilling {len(missing)} preview(s)...")

    ok_count = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, video_name in enumerate(missing, 1):
            print(f"  [{i}/{len(missing)}] {video_name} ... ", end="", flush=True)
            local_video = tmp_path / video_name
            preview_path = tmp_path / _preview_name(video_name)
            try:
                bucket.blob(video_name).download_to_filename(str(local_video))
                if not _generate_preview(local_video, preview_path):
                    print("preview generation failed, skipped")
                    continue
                bucket.blob(_preview_name(video_name)).upload_from_filename(str(preview_path), content_type="image/jpeg")
                print("OK")
                ok_count += 1
            except Exception as exc:
                logger.exception("backfill failed for %s", video_name)
                print(f"FAILED: {exc}")
            finally:
                local_video.unlink(missing_ok=True)
                preview_path.unlink(missing_ok=True)

    print(f"\nDone: {ok_count}/{len(missing)} preview(s) uploaded.")


if __name__ == "__main__":
    main()
