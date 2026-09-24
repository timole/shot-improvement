"""Builds the hand-position-annotated composed video for one or more
Android shots and publishes it to Azure Blob Storage at
"android/<stem>-composed.mp4" (spec 149) - server/android_compose.py's
own on-the-fly fallback can't afford MediaPipe pose detection inline in
an HTTP request (tens of ms per frame), so that work happens here
instead, offline, on a machine that already has core/'s full cv2 +
mediapipe stack, and the result is just served as a plain blob
afterwards.

    python tools/compose_android_shots.py                  # today's shots
    python tools/compose_android_shots.py --date 20260923   # a given day
    python tools/compose_android_shots.py shot-20260923143942-10  # specific stem(s)

With no stems given, defaults to every shot recorded on --date (today,
if omitted), audio-only shots included (they still get a composed
clip - see android_compose.render_composed). Skips a stem that already
has a composed blob unless --force is given, so re-running after an
interrupted batch only picks up where it left off.

Same auth as core/azure_sync.py / tools/publish_apk.py: `az login` +
DefaultAzureCredential (needs Storage Blob Data Contributor on the
account, which the desktop's own signed-in account already has)."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
from azure.storage.blob import ContentSettings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pose import PoseDetector, draw_palm_boxes  # noqa: E402
from server import android_blobs, android_compose  # noqa: E402

REFERENCE_STEM = "shot-20260923143942-10"  # processed first if present in the batch


def _today_prefix() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


def _rotate_and_annotate(src_video: Path, dst_video: Path) -> None:
    """Reads src_video frame by frame, rotates 90 degrees clockwise
    (correcting the missing orientation metadata - see
    android_compose's module docstring), draws yellow hand-position
    boxes (core.pose.PoseDetector, same model/logic the desktop's own
    annotated videos use), and writes an audio-less intermediate to
    dst_video - server.android_compose.render_composed(rotate=False)
    re-encodes this into the final composed file, adding the real
    audio track and the spectrogram band."""
    cap = cv2.VideoCapture(str(src_video))
    # A phone's own reported fps is rarely a round number (240.2826... on
    # real hardware, not exactly 240) - cv2.VideoWriter's mp4v/mpeg4
    # encoder derives a timebase from it and silently fails to open at
    # all (writer.isOpened() False, 0 bytes written, no exception raised
    # anywhere) whenever that timebase's denominator doesn't reduce to
    # something MPEG-4 accepts (max 65535) - confirmed on a real shot
    # whose 240.28fps tripped this while another's 240.02fps happened
    # not to. Rounding to the nearest whole number sidesteps it
    # entirely; the small drift doesn't matter since ffmpeg re-encodes
    # this whole file again right after (render_composed), audio timing
    # untouched either way.
    raw_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fps = round(raw_fps) or 30
    writer = None
    frame_index = 0
    try:
        with PoseDetector() as detector:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                if writer is None:
                    h, w = frame.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(str(dst_video), fourcc, fps, (w, h))
                    if not writer.isOpened():
                        raise RuntimeError(f"VideoWriter failed to open for {dst_video} ({w}x{h}@{fps})")
                ts_ms = int(frame_index * 1000 / fps)
                boxes = detector.detect(frame, ts_ms)
                draw_palm_boxes(frame, boxes)
                writer.write(frame)
                frame_index += 1
    finally:
        cap.release()
        if writer is not None:
            writer.release()
    if frame_index == 0:
        raise RuntimeError(f"no frames read from {src_video}")


def compose_one(stem: str, tmp_dir: Path) -> Path:
    """Downloads stem's own video (if any) + audio, draws hand boxes on
    the video, composes the final file (server.android_compose's own
    ffmpeg pass), and returns its local path - upload is the caller's
    job, so a dry run / inspection pass can call this without
    publishing anything."""
    audio_path = android_blobs.get_cached_path(stem, "wav")
    try:
        video_path: Path | None = android_blobs.get_cached_path(stem, "mp4")
    except Exception:
        video_path = None

    out_path = tmp_dir / f"{stem}-composed.mp4"
    if video_path is not None:
        annotated_path = tmp_dir / f"{stem}-annotated-raw.mp4"
        _rotate_and_annotate(video_path, annotated_path)
        android_compose.render_composed(annotated_path, audio_path, out_path, rotate=False)
    else:
        android_compose.render_composed(None, audio_path, out_path, rotate=False)
    return out_path


def publish(stem: str, local_path: Path) -> None:
    container = android_blobs._container()
    with open(local_path, "rb") as f:
        container.upload_blob(
            name=f"{android_blobs.PREFIX}{stem}-composed.mp4", data=f, overwrite=True,
            content_settings=ContentSettings(content_type="video/mp4"),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stems", nargs="*", help="specific shot stem(s) to process; default is every shot from --date")
    parser.add_argument("--date", default=None, help="yyyyMMdd; defaults to today (local date)")
    parser.add_argument("--force", action="store_true", help="rebuild even if a composed blob already exists")
    args = parser.parse_args()

    if args.stems:
        stems = list(args.stems)
    else:
        date_prefix = args.date or _today_prefix()
        all_shots = android_blobs.list_shots()
        stems = [s["stem"] for s in all_shots if s["stem"][len("shot-"):][: len(date_prefix)] == date_prefix]
        stems.sort()
        if REFERENCE_STEM in stems:
            stems.remove(REFERENCE_STEM)
            stems.insert(0, REFERENCE_STEM)

    if not stems:
        print("No shots to process.")
        return 0

    container = android_blobs._container()
    print(f"Processing {len(stems)} shot(s): {', '.join(stems)}")
    with tempfile.TemporaryDirectory(prefix="shot-compose-") as tmp:
        tmp_dir = Path(tmp)
        for i, stem in enumerate(stems, start=1):
            if not android_blobs.is_valid_stem(stem):
                print(f"[{i}/{len(stems)}] {stem}: skipped, not a valid shot stem")
                continue
            if not args.force and container.get_blob_client(f"{android_blobs.PREFIX}{stem}-composed.mp4").exists():
                print(f"[{i}/{len(stems)}] {stem}: already composed, skipping (--force to rebuild)")
                continue
            print(f"[{i}/{len(stems)}] {stem}: composing...")
            try:
                local_path = compose_one(stem, tmp_dir)
                publish(stem, local_path)
                local_path.unlink(missing_ok=True)
                print(f"[{i}/{len(stems)}] {stem}: done")
            except Exception as exc:
                print(f"[{i}/{len(stems)}] {stem}: FAILED - {exc}", file=sys.stderr)
    print("All done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
