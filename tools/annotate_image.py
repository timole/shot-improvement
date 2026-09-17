"""Annotates a single still image with hand boxes (core.pose) - for
testing/tuning that against a real photo without recording a whole
clip.

Usage:
    venv\\Scripts\\python tools\\annotate_image.py <image_path>

Writes "<stem>-annotated.jpg" next to the source image.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from core.log_setup import get_logger, setup_logging
from core.pose import PoseDetector, draw_palm_boxes

setup_logging()
logger = get_logger("annotate_image")


def annotate_image(image_path: Path) -> Path:
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise FileNotFoundError(image_path)

    with PoseDetector() as detector:
        boxes = detector.detect(frame, 0)
    print(f"hands detected: {len(boxes)}")

    out = frame.copy()
    draw_palm_boxes(out, boxes)

    out_path = image_path.with_name(f"{image_path.stem}-annotated.jpg")
    cv2.imwrite(str(out_path), out)
    return out_path


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: annotate_image.py <image_path>", file=sys.stderr)
        sys.exit(1)
    out = annotate_image(Path(sys.argv[1]))
    print(f"Done: {out}")


if __name__ == "__main__":
    main()
