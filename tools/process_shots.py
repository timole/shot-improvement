"""Precompute versioned Azure playback, hand boxes, estimates and thumbnails.

Run explicit stems first; --latest 30 limits the subsequent backfill.
Original video/audio/phone metadata are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
from azure.storage.blob import ContentSettings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.pose import PoseDetector, draw_palm_boxes
from core.hand_speed import body_pixels, estimate_speed
from server import android_blobs as blobs, android_compose as compose, shot_index


def process(stem, directory, height_m, hand):
    directory.mkdir(parents=True, exist_ok=True)
    container = blobs._container()
    for ext in ("mp4", "wav", "json"):
        path = directory / f"{stem}.{ext}"
        if not path.exists():
            path.write_bytes(container.get_blob_client(f"android/{path.name}").download_blob().readall())
    meta = json.loads((directory / f"{stem}.json").read_text(encoding="utf-8"))
    shot_t = meta.get("shotT", 1.)
    normalized = directory / f"{stem}-normalized.mp4"
    # Respect original PTS: frame_index / reported average fps is wrong for
    # variable-frame-rate phone clips, especially duplicated preroll frames.
    compose._run(["-i", str(directory / f"{stem}.mp4"), "-an", "-vf", "transpose=1,fps=240",
                  "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", str(normalized)])
    cap = cv2.VideoCapture(str(normalized))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    annotated = directory / f"{stem}-annotated.mp4"
    writer = cv2.VideoWriter(str(annotated), cv2.VideoWriter_fourcc(*"mp4v"), 240, (w, h))
    if not writer.isOpened():
        raise RuntimeError("Could not open annotation encoder")
    samples, frame_index, boxes, visible = [], 0, [], 0
    try:
        with PoseDetector() as detector:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                t = frame_index / 240
                if frame_index % 4 == 0 or shot_t - .15 <= t <= shot_t + .1:
                    boxes = detector.detect(frame, round(t * 1000))
                    sample = {"t": t, **{b.label: [b.cx, b.cy] for b in boxes}}
                    if detector.last_landmarks:
                        sample["body_px"] = body_pixels(detector.last_landmarks[0], w, h)
                    samples.append(sample)
                draw_palm_boxes(frame, boxes)
                visible += bool(boxes)
                writer.write(frame)
                if abs(t - shot_t) < 1 / 480:
                    cv2.imwrite(str(directory / f"{stem}-review.jpg"), frame)
                frame_index += 1
    finally:
        cap.release()
        writer.release()
    if not frame_index:
        raise RuntimeError("No decoded frames")
    speed = estimate_speed(samples, shot_t, height_m, hand)
    out = directory / f"{stem}-composed.mp4"
    compose.render_composed(annotated, directory / f"{stem}.wav", out, rotate=False)
    version = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    thumbnail = directory / f"{stem}-thumb.jpg"
    compose._run(["-ss", str(shot_t), "-i", str(out), "-frames:v", "1", "-vf", "scale=160:-2", str(thumbnail)])
    playback = {"duration_s": compose.probe_duration_s(out), "fps": compose.probe_fps(out),
                "frame_count": compose.probe_frame_count(out)}
    row = {"stem": stem, "analysis_version": version, "top_hand_speed_kmh": speed,
           "hand_speed_method": "camera_plane_height_scaled_50ms", "height_m": height_m,
           "top_hand": hand, "playback": playback, "hand_box_coverage": visible / frame_index}
    (directory / f"{stem}-analysis.json").write_text(json.dumps({**row, "samples": samples}), encoding="utf-8")
    for path, extension, mime in ((out, "mp4", "video/mp4"), (thumbnail, "jpg", "image/jpeg")):
        with path.open("rb") as stream:
            container.upload_blob(f"android/processed/{stem}-{version}.{extension}", stream, overwrite=True,
                                  content_settings=ContentSettings(content_type=mime))
    shot_index.update([row])
    normalized.unlink(missing_ok=True)
    annotated.unlink(missing_ok=True)
    print(json.dumps(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stems", nargs="*")
    parser.add_argument("--latest", type=int)
    parser.add_argument("--height-cm", type=float, required=True)
    parser.add_argument("--top-hand", choices=["left", "right"], required=True)
    parser.add_argument("--directory", type=Path, default=Path("recordings/spec162"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--calibrate-only", action="store_true", help="Recalculate speeds from saved tracks without re-encoding")
    args = parser.parse_args()
    rows = shot_index.list_shots()
    stems = args.stems or [row["stem"] for row in rows if row.get("has_video")][:args.latest or 0]
    if not stems:
        parser.error("Provide stems or --latest")
    for stem in stems:
        if not blobs.is_valid_stem(stem):
            parser.error("Invalid stem")
        row = next((r for r in rows if r["stem"] == stem), {})
        if args.calibrate_only:
            path = args.directory / f"{stem}-analysis.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            meta = json.loads((args.directory / f"{stem}.json").read_text(encoding="utf-8"))
            data.update(height_m=args.height_cm / 100, top_hand=args.top_hand + " hand")
            data["top_hand_speed_kmh"] = estimate_speed(data["samples"], meta.get("shotT", 1), data["height_m"], data["top_hand"])
            path.write_text(json.dumps(data), encoding="utf-8")
            shot_index.update([{k: v for k, v in data.items() if k != "samples"}])
            print(stem, data["top_hand_speed_kmh"], flush=True)
            continue
        if row.get("analysis_version") and not args.force:
            print(f"{stem}: already processed", flush=True)
            continue
        print(f"{stem}: processing", flush=True)
        process(stem, args.directory, args.height_cm / 100, args.top_hand + " hand")


if __name__ == "__main__":
    main()
