"""MediaPipe PoseLandmarker wrapper: palm-center estimation and box drawing.

BlazePose's 33-point body topology has no palm-center landmark
directly, so the palm position is estimated by averaging the wrist
landmark with the pinky- and index-finger knuckle landmarks on the
same side - the same approach used in the earlier browser spike
(specs 078/079, now removed in favor of this native app).
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

from .log_setup import get_logger

logger = get_logger("pose")

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
)
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "pose_landmarker_lite.task"

VISIBILITY_THRESHOLD = 0.5
BOX_SIZE = 80
BOX_COLOR_BGR = (0, 200, 83)  # OpenCV drawing is BGR, not RGB

# (wrist, pinky-knuckle, index-knuckle, label) - BlazePose indices.
PALM_LANDMARKS = [
    (15, 17, 19, "vasen kasi"),
    (16, 18, 20, "oikea kasi"),
]


def ensure_model_downloaded(model_path: Path = MODEL_PATH) -> Path:
    if not model_path.exists():
        logger.info("Downloading pose model from %s to %s", MODEL_URL, model_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, model_path)
        logger.info("Pose model downloaded (%d bytes)", model_path.stat().st_size)
    return model_path


@dataclass(frozen=True)
class PalmBox:
    cx: float
    cy: float
    label: str


def _visible(landmark) -> bool:
    visibility = getattr(landmark, "visibility", None)
    return visibility is None or visibility >= VISIBILITY_THRESHOLD


def palm_boxes_from_landmarks(
    pose_landmarks_list: Sequence[Sequence[object]], frame_width: int, frame_height: int
) -> list[PalmBox]:
    """Pure function (no model/camera needed) - takes MediaPipe's own
    pose_landmarks result shape (one list of 33 landmarks per detected
    person) and returns the palm boxes to draw, in pixel coordinates."""
    boxes = []
    for landmarks in pose_landmarks_list:
        for wrist_i, pinky_i, index_i, label in PALM_LANDMARKS:
            w, p, idx = landmarks[wrist_i], landmarks[pinky_i], landmarks[index_i]
            if not (_visible(w) and _visible(p) and _visible(idx)):
                continue
            cx = (w.x + p.x + idx.x) / 3 * frame_width
            cy = (w.y + p.y + idx.y) / 3 * frame_height
            boxes.append(PalmBox(cx, cy, label))
    return boxes


def draw_palm_boxes(frame_bgr: np.ndarray, boxes: Sequence[PalmBox]) -> None:
    half = BOX_SIZE // 2
    for box in boxes:
        x, y = int(box.cx), int(box.cy)
        cv2.rectangle(frame_bgr, (x - half, y - half), (x + half, y + half), BOX_COLOR_BGR, 4)
        cv2.putText(
            frame_bgr, box.label, (x - half, max(y - half - 8, 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, BOX_COLOR_BGR, 2, cv2.LINE_AA,
        )


class PoseDetector:
    """Thin wrapper around MediaPipe's PoseLandmarker in VIDEO running
    mode, which requires a strictly increasing timestamp per detect()
    call on the same instance - see detect()'s ts_ms guard."""

    def __init__(self, model_path: Path | None = None):
        resolved = ensure_model_downloaded(model_path or MODEL_PATH)
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(resolved)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self._last_ts_ms = -1
        logger.info("PoseDetector created")

    def detect(self, frame_bgr: np.ndarray, ts_ms: int) -> list[PalmBox]:
        # VIDEO mode requires each timestamp to be strictly greater than
        # the last - guards against two frames landing in the same ms.
        ts_ms = max(ts_ms, self._last_ts_ms + 1)
        self._last_ts_ms = ts_ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(mp_image, ts_ms)
        h, w = frame_bgr.shape[:2]
        return palm_boxes_from_landmarks(result.pose_landmarks, w, h)

    def close(self) -> None:
        self._landmarker.close()
        logger.info("PoseDetector closed")

    def __enter__(self) -> "PoseDetector":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def annotate_frames_dir(
    raw_dir: Path,
    annotated_dir: Path,
    extension: str,
    fps: float,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Runs pose detection over an already-captured sequence of raw
    frame files and writes palm-box-annotated copies into annotated_dir
    (same filenames).

    Spec 086: pose inference (~57ms/frame, measured - see recorder.py)
    used to run inside the live capture loop, on every frame, while
    recording - it was the single biggest cost keeping real captured
    fps low, at a point where the user had already reported the fps
    was too low to see a fast stick-bend at all. Deferring it to here -
    a pass over already-saved files, run after capture ends where
    speed no longer matters as much - lets the capture loop do only a
    camera read + one disk write per frame, capturing far more frames
    per second of real time (confirmed live - see spec 086).

    on_progress(done, total), if given, is called after each frame - the
    GUI uses this to show a percentage while this pass runs (spec 090),
    which on this hardware is slow enough (~57ms/frame) to be worth
    showing progress for rather than a single static status line."""
    frame_paths = sorted(raw_dir.glob(f"*.{extension}"))
    total = len(frame_paths)
    if not frame_paths:
        return
    with PoseDetector() as detector:
        for i, path in enumerate(frame_paths):
            frame = cv2.imread(str(path))
            if frame is None:
                if on_progress:
                    on_progress(i + 1, total)
                continue
            ts_ms = int(i * 1000 / fps) if fps > 0 else i
            boxes = detector.detect(frame, ts_ms)
            annotated = frame.copy()
            draw_palm_boxes(annotated, boxes)
            cv2.imwrite(str(annotated_dir / path.name), annotated)
            if on_progress:
                on_progress(i + 1, total)
