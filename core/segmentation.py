"""On-demand background removal for a single displayed frame (the
"Häivytä tausta" button in the playback controls).

Known, confirmed limitation: MediaPipe's selfie segmenter identifies
the PERSON specifically - it has no notion of a held object. Tested
directly against a real recorded frame showing a raised hockey stick:
the person was cleanly isolated, but the stick was removed entirely
along with the rest of the background. This module only ever isolates
the person; "and the stick" from the original request isn't
achievable with this model, and isn't silently pretended otherwise.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

from .log_setup import get_logger

logger = get_logger("segmentation")

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
    "selfie_segmenter/float16/latest/selfie_segmenter.tflite"
)
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "selfie_segmenter.tflite"


def ensure_model_downloaded(model_path: Path = MODEL_PATH) -> Path:
    if not model_path.exists():
        logger.info("Downloading segmentation model from %s to %s", MODEL_URL, model_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, model_path)
        logger.info("Segmentation model downloaded (%d bytes)", model_path.stat().st_size)
    return model_path


def apply_person_mask(frame_bgr: np.ndarray, category_mask: np.ndarray) -> np.ndarray:
    """Pure function (no model needed) - the actual pixel logic, kept
    separate from the MediaPipe call so it's unit-testable. This
    model's category_mask is 0 for the person, non-zero for
    background (confirmed empirically, not assumed - see module
    docstring)."""
    mask2d = category_mask.reshape(frame_bgr.shape[0], frame_bgr.shape[1])
    out = frame_bgr.copy()
    out[mask2d != 0] = 0
    return out


class BackgroundRemover:
    def __init__(self, model_path: Optional[Path] = None):
        resolved = ensure_model_downloaded(model_path or MODEL_PATH)
        # Same call shape as the already-reliable core.pose.PoseDetector
        # (no explicit delegate - MediaPipe's Python Tasks API defaults
        # to CPU). A hang was observed once when this ran synchronously
        # on the GUI's main thread while a camera + PoseDetector were
        # already active; see gui.py's on_remove_background_click for
        # the actual fix (runs on a background thread, like spec 083's
        # ffmpeg encode) - not a delegate issue.
        options = vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=str(resolved)),
            running_mode=vision.RunningMode.IMAGE,
            output_category_mask=True,
        )
        self._segmenter = vision.ImageSegmenter.create_from_options(options)
        logger.info("BackgroundRemover created")

    def remove_background(self, frame_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._segmenter.segment(mp_image)
        return apply_person_mask(frame_bgr, result.category_mask.numpy_view())

    def close(self) -> None:
        self._segmenter.close()
        logger.info("BackgroundRemover closed")
