"""Renders a full-clip spectrogram from the recorded audio, and
composites it (with a moving playhead line marking each frame's own
position in time) beneath every annotated frame - so a clip's
-annotated.mp4 lets a person visually spot a loud transient (e.g. a
stick-to-ball impact) and see exactly what's on screen at that
instant, without needing to build automatic audio-impact-detection
first. See spec 085.

Computed once per recording from the WHOLE captured audio buffer, not
per frame - an FFT per frame during live capture would slow down the
already CPU-constrained capture loop (specs 078-083). This runs as a
step after capture ends, over the already-written annotated frame
files, before they're handed to ffmpeg - in the GUI, that's already
safely on the background encode thread (spec 083), not the live
camera loop.

Only the annotated clip gets this; the raw clip stays at the camera's
native resolution, per explicit request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from .log_setup import get_logger
from .profiling import NULL_PROFILER, Profiler

logger = get_logger("spectrogram")

# The composited annotated frame stacks three bands - video, this
# spectrogram+playhead panel, and (spec 098) a claps/puck-speed
# annotation band below it. core.compose owns the actual 3-band layout
# (CLAPS_BAND_HEIGHT lives there, not here) - this is just this panel's
# own height, shrunk from 480 in spec 098 to make room for the new band
# beneath it, same 1280x1200 total either way.
SPECTROGRAM_HEIGHT = 280
FFT_WINDOW = 512
FFT_HOP = 128
DYNAMIC_RANGE_DB = 80.0
PLAYHEAD_COLOR_BGR = (0, 0, 255)  # red - OpenCV drawing is BGR
PLAYHEAD_THICKNESS = 2


def compute_spectrogram_image(audio: np.ndarray, width: int, height: int) -> np.ndarray:
    """A width x height x 3 BGR image: time left-to-right, frequency
    low (bottom) to high (top), brightness/color = log-magnitude."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float64)
    if len(audio) < FFT_WINDOW:
        audio = np.pad(audio, (0, FFT_WINDOW - len(audio)))

    window = np.hanning(FFT_WINDOW)
    n_frames = max(1, (len(audio) - FFT_WINDOW) // FFT_HOP + 1)
    magnitudes = np.zeros((FFT_WINDOW // 2, n_frames))
    for i in range(n_frames):
        start = i * FFT_HOP
        chunk = audio[start : start + FFT_WINDOW]
        if len(chunk) < FFT_WINDOW:
            chunk = np.pad(chunk, (0, FFT_WINDOW - len(chunk)))
        spectrum = np.fft.rfft(chunk * window)
        magnitudes[:, i] = np.abs(spectrum[: FFT_WINDOW // 2])

    db = 20 * np.log10(magnitudes + 1e-6)
    db = np.clip(db, db.max() - DYNAMIC_RANGE_DB, db.max())
    span = max(db.max() - db.min(), 1e-6)
    normalized = (db - db.min()) / span

    resized = cv2.resize(normalized.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR)
    resized = np.flipud(resized)  # low frequency at the bottom
    gray = (resized * 255).astype(np.uint8)
    return cv2.applyColorMap(gray, cv2.COLORMAP_VIRIDIS)


def with_playhead(spectrogram: np.ndarray, x_fraction: float) -> np.ndarray:
    img = spectrogram.copy()
    x = int(np.clip(x_fraction, 0.0, 1.0) * (img.shape[1] - 1))
    cv2.line(img, (x, 0), (x, img.shape[0] - 1), PLAYHEAD_COLOR_BGR, PLAYHEAD_THICKNESS)
    return img


def add_spectrograms_to_frames(
    frames_dir: Path,
    audio: Optional[np.ndarray],
    frame_count: int,
    extension: str,
    width: int,
    on_progress: Optional[Callable[[int, int], None]] = None,
    profiler: Profiler = NULL_PROFILER,
) -> bool:
    """Rewrites each frame_%06d.<extension> in frames_dir in place,
    stacking [original frame; spectrogram-with-playhead] to double its
    height. Returns False (leaving frames untouched) if there's no
    audio to render.

    on_progress(done, total), if given, is called after each frame -
    same convention as core.pose.annotate_frames_dir (spec 090).

    profiler (spec 092), if given, times the whole pass under
    "add_spectrograms_to_frames", the one-off FFT separately under
    "spectrogram:fft", and the per-frame loop under
    "spectrogram:imread"/"spectrogram:playhead"/"spectrogram:vstack"/
    "spectrogram:imwrite" - a no-op when profiler is NULL_PROFILER."""
    if audio is None or frame_count == 0 or len(audio) == 0:
        logger.warning("add_spectrograms_to_frames: no audio, skipping (frame_count=%d)", frame_count)
        return False

    with profiler.stage("add_spectrograms_to_frames"):
        with profiler.accum("spectrogram:fft"):
            spectrogram = compute_spectrogram_image(audio, width, SPECTROGRAM_HEIGHT)
        for i in range(frame_count):
            path = frames_dir / f"frame_{i:06d}.{extension}"
            with profiler.accum("spectrogram:imread"):
                frame = cv2.imread(str(path))
            if frame is not None:
                x_fraction = i / max(frame_count - 1, 1)
                with profiler.accum("spectrogram:playhead"):
                    panel = with_playhead(spectrogram, x_fraction)
                with profiler.accum("spectrogram:vstack"):
                    composite = np.vstack([frame, panel])
                with profiler.accum("spectrogram:imwrite"):
                    cv2.imwrite(str(path), composite)
            if on_progress:
                on_progress(i + 1, frame_count)

    logger.info("add_spectrograms_to_frames: composited %d frames in %s", frame_count, frames_dir)
    return True
