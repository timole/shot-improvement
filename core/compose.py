"""Fused pose-annotation + spectrogram-compositing pass (spec 092).

Measured on this hardware (SHOT_IMPROVEMENT_PROFILE=1, a real 3s / 82-
frame clip): the old two-pass pipeline spent 127ms/frame in
annotate_frames_dir (55ms detect + 51ms imread + 5ms imwrite) and then
65ms/frame in add_spectrograms_to_frames, of which 48ms/frame (74% of
that stage) was cv2.imread of the *exact same file* annotate_frames_dir
had just written a moment earlier - reading back bytes this process
itself just wrote, purely to draw a spectrogram panel underneath.

This module replaces both with one pass that opens each raw frame
exactly once: detect -> draw palm boxes IN PLACE (no frame.copy()) ->
composite the spectrogram+playhead panel into one preallocated buffer
(no per-frame np.vstack allocation, no per-frame spectrogram.copy())
-> write once. It eliminates a full read+write cycle of the annotated
frames entirely (previously: annotate wrote annotated/, then
spectrogram read that back and rewrote it, bigger).

Pixel-identical to the old pipeline by construction - see
tests/test_compose.py, which checks this fused path directly against
core.pose.draw_palm_boxes and core.spectrogram.with_playhead (both
kept in place specifically to serve as that reference, even though
this module no longer calls them in the hot path).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

from .log_setup import get_logger
from .pose import PalmBox, PoseDetector, draw_palm_boxes
from .profiling import NULL_PROFILER, Profiler
from .spectrogram import (
    PLAYHEAD_COLOR_BGR,
    PLAYHEAD_THICKNESS,
    SPECTROGRAM_HEIGHT,
    compute_spectrogram_image,
)

logger = get_logger("compose")


def composite_into(
    top: np.ndarray,
    bottom: np.ndarray,
    frame: np.ndarray,
    boxes: Sequence[PalmBox],
    spectrogram: np.ndarray,
    x_fraction: float,
) -> None:
    """The pure per-frame compositing step, factored out of
    compose_annotated_frames' loop so it's directly unit-testable
    against the reference building blocks (draw_palm_boxes,
    with_playhead) without needing a real PoseDetector or files - see
    tests/test_compose.py.

    top/bottom must be C-contiguous views into the same backing array
    (as compose_annotated_frames' preallocated `composite` provides) -
    writes go directly into them, no allocation, no copy. Draws boxes
    onto `frame` in place, then copies it into `top` (still one copy,
    same as the old pipeline's frame.copy() would have been one copy -
    the elimination is not paying for THIS copy twice, once here and
    once in add_spectrograms_to_frames' cv2.imread of the written
    file)."""
    draw_palm_boxes(frame, boxes)
    top[:] = frame
    bottom[:] = spectrogram
    x = int(np.clip(x_fraction, 0.0, 1.0) * (bottom.shape[1] - 1))
    cv2.line(bottom, (x, 0), (x, bottom.shape[0] - 1), PLAYHEAD_COLOR_BGR, PLAYHEAD_THICKNESS)


def compose_annotated_frames(
    raw_dir: Path,
    annotated_dir: Path,
    extension: str,
    fps: float,
    audio: Optional[np.ndarray],
    frame_count: int,
    width: int,
    height: int,
    frame_times: Optional[Sequence[float]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    profiler: Profiler = NULL_PROFILER,
) -> bool:
    """Runs pose detection AND spectrogram compositing over an already-
    captured sequence of raw frame files in one pass, writing
    palm-box-annotated, spectrogram-paneled copies into annotated_dir
    (same filenames) - replaces the old annotate_frames_dir() ->
    add_spectrograms_to_frames() two-pass pipeline (spec 092; see
    module docstring for the measured cost that motivated this).

    frame_times (spec 097), if given, is each frame's real capture
    timestamp (seconds from capture start) - used both for the pose
    detector's clock and, more importantly, for the spectrogram
    playhead's x position, which otherwise (frame index / total frames,
    assuming even spacing) drifted out of sync with the real audio
    position on this hardware's unevenly-timed captures the same way
    the old constant-"-framerate" video encode did (see
    core.recorder's module docstring). Falls back to the old
    even-spacing assumption when not given or mismatched in length.

    Returns False (leaving annotated_dir untouched) if there's no
    audio to render a spectrogram from - same convention as the old
    add_spectrograms_to_frames.

    on_progress(done, total), if given, is called once per frame -
    same convention as the two functions this replaces (spec 090).

    profiler (spec 092), if given, times the whole pass under
    "compose_annotated_frames" and the per-frame loop under
    "compose:imread"/"compose:detect"/"compose:draw"/"compose:panel"/
    "compose:imwrite" - a no-op when profiler is NULL_PROFILER."""
    if audio is None or frame_count == 0 or len(audio) == 0:
        logger.warning("compose_annotated_frames: no audio, skipping (frame_count=%d)", frame_count)
        return False

    frame_paths = sorted(raw_dir.glob(f"*.{extension}"))
    total = len(frame_paths)
    if not frame_paths:
        return False

    with profiler.stage("compose_annotated_frames"):
        with profiler.accum("compose:fft"):
            spectrogram = compute_spectrogram_image(audio, width, SPECTROGRAM_HEIGHT)

        # One buffer, reused every frame (spec 092) - was two
        # allocations per frame in the old pipeline (frame.copy() in
        # pose.annotate_frames_dir, np.vstack(...) in
        # spectrogram.add_spectrograms_to_frames). `top`/`bottom` are
        # C-contiguous row-slice VIEWS into `composite`, not copies -
        # writing into either writes directly into `composite`, and
        # cv2 functions operate in place on a C-contiguous view exactly
        # as they would on an owned array of the same shape (asserted
        # in tests/test_compose.py, since a non-contiguous view would
        # make cv2 silently copy and the in-place draw would be lost).
        composite = np.empty((height + SPECTROGRAM_HEIGHT, width, 3), dtype=np.uint8)
        top = composite[:height]
        bottom = composite[height:]

        have_real_times = frame_times is not None and len(frame_times) == total
        # Audio's own real duration is the timeline the playhead needs to
        # track (spec 097) - not "total frames", which on this hardware's
        # unevenly-spaced captures is not proportional to real time.
        # Local import: .recorder imports compose_annotated_frames from
        # this module, so importing SAMPLE_RATE at module level here
        # would be circular.
        from .recorder import SAMPLE_RATE
        audio_duration_s = len(audio) / SAMPLE_RATE if audio is not None else 0.0

        with profiler.accum("compose:model_load"):
            detector_cm = PoseDetector()
        with detector_cm as detector:
            for i, path in enumerate(frame_paths):
                with profiler.accum("compose:imread"):
                    frame = cv2.imread(str(path))
                if frame is None:
                    if on_progress:
                        on_progress(i + 1, total)
                    continue
                frame_time_s = frame_times[i] if have_real_times else (i / fps if fps > 0 else float(i))
                ts_ms = int(frame_time_s * 1000)
                with profiler.accum("compose:detect"):
                    # boxes must be computed from the UNdrawn frame -
                    # composite_into() draws onto `frame` in place
                    # right after this, so detect() must run first.
                    boxes = detector.detect(frame, ts_ms)
                with profiler.accum("compose:draw+panel"):
                    if have_real_times and audio_duration_s > 0:
                        x_fraction = frame_time_s / audio_duration_s
                    else:
                        x_fraction = i / max(total - 1, 1)
                    composite_into(top, bottom, frame, boxes, spectrogram, x_fraction)
                with profiler.accum("compose:imwrite"):
                    cv2.imwrite(str(annotated_dir / path.name), composite)
                if on_progress:
                    on_progress(i + 1, total)

    logger.info("compose_annotated_frames: composed %d frames in %s", total, annotated_dir)
    return True
