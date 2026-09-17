"""Fused pose-annotation + spectrogram-compositing pass (spec 092),
extended (spec 098) with a claps/puck-speed annotation band.

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

Spec 098 adds a third band below the spectrogram: a per-clap timestamp
strip, plus (for each chronologically-paired shot/hit) a yellow line
and speed annotation baked directly into the spectrogram band. Unlike
the moving playhead, none of this changes frame to frame within one
clip, so it's rendered ONCE before the per-frame loop and just copied
into the composite buffer every frame - composite_into() itself, and
its existing pixel-identity tests, are untouched by this.

Spec 099 adds save_shot_images(): a plain (unannotated, native-
resolution) JPEG per detected shot, with just its puck speed burned in
- a separate, simpler artifact from the -annotated.mp4 above, meant to
be quickly skimmed or shared on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

from .claps import detect_claps, pair_claps, puck_speed_kmh
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

# Spec 098: a claps/puck-speed annotation band below the spectrogram -
# same 1280x1200 total composite height as before spec 098 (720 video +
# 280 spectrogram, shrunk from 480 - + 200 here).
CLAPS_BAND_HEIGHT = 200
CLAPS_BAND_BG_BGR = (0, 0, 0)
CLAP_TICK_COLOR_BGR = (255, 255, 255)
CLAP_TICK_HEIGHT = 14
CLAP_LABEL_ROWS = 4  # cycled so nearby clap labels never overlap
CLAP_FONT = cv2.FONT_HERSHEY_SIMPLEX
CLAP_FONT_SCALE = 0.5
CLAP_FONT_THICKNESS = 1
CLAP_TEXT_COLOR_BGR = (255, 255, 255)

# Local row within the spectrogram band (0..SPECTROGRAM_HEIGHT) - the
# user's own spec was "~740px" against the full composite, where the
# spectrogram band starts at absolute row FRAME_HEIGHT (720), so 20
# here is that same position expressed relative to the band itself.
PAIR_LINE_Y = 20
PAIR_LINE_COLOR_BGR = (0, 255, 255)  # yellow (BGR)
PAIR_LINE_THICKNESS = 3
PAIR_TEXT_Y_OFFSET = 26  # below the line
PAIR_FONT = cv2.FONT_HERSHEY_SIMPLEX
PAIR_FONT_SCALE = 0.6
PAIR_FONT_THICKNESS = 2

# All outlined text/lines: a thicker black pass first, then the real
# color on top - drawn once per clip (not per frame), cheap, and keeps
# labels legible over the busy viridis spectrogram background.
OUTLINE_COLOR_BGR = (0, 0, 0)
OUTLINE_EXTRA_THICKNESS = 2


def _x_for_time(t: float, duration_s: float, width: int) -> int:
    """Same left-to-right time axis the moving playhead already uses
    (frame_time_s / audio_duration_s, clipped to the visible width) -
    the one formula both the playhead and every clap/pair annotation
    below share, so they can never drift apart."""
    fraction = t / duration_s if duration_s > 0 else 0.0
    return int(np.clip(fraction, 0.0, 1.0) * (width - 1))


def _put_outlined_text(img: np.ndarray, text: str, x: int, y: int, font, scale: float, color, thickness: int) -> None:
    cv2.putText(img, text, (x, y), font, scale, OUTLINE_COLOR_BGR, thickness + OUTLINE_EXTRA_THICKNESS, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def _clamp_text_x(img_width: int, x: int, text: str, font, scale: float, thickness: int) -> int:
    (text_w, _), _ = cv2.getTextSize(text, font, scale, thickness)
    return int(np.clip(x, 0, max(img_width - text_w, 0)))


def render_claps_band(width: int, height: int, claps: Sequence[float], duration_s: float) -> np.ndarray:
    """A short timestamp label per detected clap, positioned along the
    same time axis as the spectrogram/playhead - not per-frame content,
    built once per clip and copied into the composite unchanged (see
    module docstring)."""
    band = np.zeros((height, width, 3), dtype=np.uint8)
    band[:] = CLAPS_BAND_BG_BGR
    for i, t in enumerate(claps):
        x = _x_for_time(t, duration_s, width)
        cv2.line(band, (x, 0), (x, CLAP_TICK_HEIGHT), CLAP_TICK_COLOR_BGR, 1, cv2.LINE_AA)
        row = i % CLAP_LABEL_ROWS
        y = CLAP_TICK_HEIGHT + (row + 1) * (height - CLAP_TICK_HEIGHT) // (CLAP_LABEL_ROWS + 1)
        text = f"{t:.2f}"
        text_x = _clamp_text_x(width, x, text, CLAP_FONT, CLAP_FONT_SCALE, CLAP_FONT_THICKNESS)
        cv2.putText(band, text, (text_x, y), CLAP_FONT, CLAP_FONT_SCALE, CLAP_TEXT_COLOR_BGR, CLAP_FONT_THICKNESS, cv2.LINE_AA)
    return band


def draw_pair_annotations(spectrogram: np.ndarray, pairs: Sequence[tuple[float, float]], duration_s: float) -> None:
    """Draws each paired (shot_t, hit_t)'s travel-time line + speed
    directly onto the spectrogram image, in place - baked in once per
    clip (see module docstring), not redrawn per frame."""
    width = spectrogram.shape[1]
    for shot_t, hit_t in pairs:
        x1 = _x_for_time(shot_t, duration_s, width)
        x2 = _x_for_time(hit_t, duration_s, width)
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        cv2.line(spectrogram, (lo, PAIR_LINE_Y), (hi, PAIR_LINE_Y), OUTLINE_COLOR_BGR, PAIR_LINE_THICKNESS + OUTLINE_EXTRA_THICKNESS, cv2.LINE_AA)
        cv2.line(spectrogram, (lo, PAIR_LINE_Y), (hi, PAIR_LINE_Y), PAIR_LINE_COLOR_BGR, PAIR_LINE_THICKNESS, cv2.LINE_AA)

        speed = puck_speed_kmh(shot_t, hit_t)
        if speed is None:
            continue
        text = f"{round(speed)} km/h"
        text_x = _clamp_text_x(width, (lo + hi) // 2, text, PAIR_FONT, PAIR_FONT_SCALE, PAIR_FONT_THICKNESS)
        _put_outlined_text(
            spectrogram, text, text_x, PAIR_LINE_Y + PAIR_TEXT_Y_OFFSET,
            PAIR_FONT, PAIR_FONT_SCALE, PAIR_LINE_COLOR_BGR, PAIR_FONT_THICKNESS,
        )


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
    palm-box-annotated, spectrogram-paneled, claps-annotated copies into
    annotated_dir (same filenames) - replaces the old
    annotate_frames_dir() -> add_spectrograms_to_frames() two-pass
    pipeline (spec 092; see module docstring for the measured cost that
    motivated this).

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
    "compose_annotated_frames", the one-off claps detection+rendering
    under "compose:claps" (spec 098), and the per-frame loop under
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
        # Audio's own real duration is the timeline the playhead needs to
        # track (spec 097) - not "total frames", which on this hardware's
        # unevenly-spaced captures is not proportional to real time.
        # Local import: .recorder imports compose_annotated_frames from
        # this module, so importing SAMPLE_RATE at module level here
        # would be circular.
        from .recorder import SAMPLE_RATE
        audio_duration_s = len(audio) / SAMPLE_RATE if audio is not None else 0.0

        with profiler.accum("compose:fft"):
            spectrogram = compute_spectrogram_image(audio, width, SPECTROGRAM_HEIGHT)

        with profiler.accum("compose:claps"):
            # Spec 098: claps/puck-speed annotations. Unlike the moving
            # playhead, none of this changes frame to frame within one
            # clip - the pair lines get baked directly into `spectrogram`
            # (so every frame's `bottom[:] = spectrogram` below picks
            # them up for free) and the claps band is built once, not
            # redrawn per frame.
            claps = detect_claps(audio, SAMPLE_RATE)
            clap_times = [t for t, _peak_rms in claps]
            pairs, _trailing_unpaired = pair_claps(clap_times)
            draw_pair_annotations(spectrogram, pairs, audio_duration_s)
            claps_band = render_claps_band(width, CLAPS_BAND_HEIGHT, clap_times, audio_duration_s)

        # One buffer, reused every frame (spec 092) - was two
        # allocations per frame in the old pipeline (frame.copy() in
        # pose.annotate_frames_dir, np.vstack(...) in
        # spectrogram.add_spectrograms_to_frames). `top`/`bottom`/
        # `claps_view` are C-contiguous row-slice VIEWS into `composite`,
        # not copies - writing into any of them writes directly into
        # `composite`, and cv2 functions operate in place on a
        # C-contiguous view exactly as they would on an owned array of
        # the same shape (asserted in tests/test_compose.py, since a
        # non-contiguous view would make cv2 silently copy and the
        # in-place draw would be lost).
        composite = np.empty((height + SPECTROGRAM_HEIGHT + CLAPS_BAND_HEIGHT, width, 3), dtype=np.uint8)
        top = composite[:height]
        bottom = composite[height : height + SPECTROGRAM_HEIGHT]
        claps_view = composite[height + SPECTROGRAM_HEIGHT :]
        # Written once, here - the loop below (and composite_into(), see
        # its own docstring) never touches these rows again.
        claps_view[:] = claps_band

        have_real_times = frame_times is not None and len(frame_times) == total

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


# Spec 099: a plain, unannotated snapshot per shot - native capture
# resolution (no pose boxes, no spectrogram), just the puck-speed label
# burned in. Deliberately separate constants from the claps-band/pair-
# line ones above - this is a different-sized label on a different kind
# of image (a single full-resolution frame, not a thin annotation band).
SHOT_IMAGE_FONT = cv2.FONT_HERSHEY_SIMPLEX
SHOT_IMAGE_FONT_PX = 50  # target glyph height, not a cv2 "scale"
SHOT_IMAGE_LABEL_COLOR_BGR = (255, 255, 255)  # white
SHOT_IMAGE_MARGIN_BOTTOM = 30
SHOT_IMAGE_JPEG_QUALITY = 92


def _draw_bottom_centered_label(frame: np.ndarray, text: str, font_px: int, color) -> None:
    """White-on-black-outline text, horizontally centered, its
    baseline a fixed margin above the bottom edge. font_px is a target
    glyph height in pixels - cv2 only takes an abstract "scale", so
    it's converted via one reference measurement at scale=1.0."""
    (_, ref_h), _ = cv2.getTextSize("0", SHOT_IMAGE_FONT, 1.0, 1)
    scale = font_px / ref_h
    thickness = max(2, round(scale * 1.2))
    (text_w, _text_h), _baseline = cv2.getTextSize(text, SHOT_IMAGE_FONT, scale, thickness)
    x = (frame.shape[1] - text_w) // 2
    y = frame.shape[0] - SHOT_IMAGE_MARGIN_BOTTOM
    _put_outlined_text(frame, text, x, y, SHOT_IMAGE_FONT, scale, color, thickness)


def save_shot_images(
    raw_dir: Path,
    extension: str,
    frame_times: Sequence[float],
    audio: np.ndarray,
    sample_rate: int,
    out_dir: Path,
    filename_stem: str,
) -> list[Path]:
    """For each detected "shot" - the first half of every chronologically
    paired shot/hit clap (core.claps.pair_claps), plus a trailing
    unpaired final shot if the clip has one - saves the RAW camera
    frame closest to that shot's real peak timestamp as a JPEG at
    native capture resolution (no spectrogram - a different, simpler
    image than the -annotated.mp4 this clip also gets), with hand
    boxes (spec 102 - core.pose, yellow) and the computed puck speed
    (white, black-outlined text at the bottom). The trailing unpaired
    shot (no corresponding hit, so no computable speed) still gets an
    image, with hand boxes but no speed text.

    Filenames: "<filename_stem>-shot-01.jpg", "-shot-02.jpg", ... in
    chronological order (2-digit, matching a typical single clip's
    shot count - see spec 099 if this ever needs 3 digits). Returns
    the list of saved paths, in that same order.

    Runs its own PoseDetector (spec 102) - by the time this is called,
    compose_annotated_frames' own detector has already been created
    and closed, so this can't reuse one; only a handful of frames need
    it (one per shot, not per clip frame), so the model-load cost is
    paid once per call, not once per frame."""
    claps = detect_claps(audio, sample_rate)
    clap_times = [t for t, _peak_rms in claps]
    pairs, trailing = pair_claps(clap_times)

    shots: list[tuple[float, Optional[float]]] = list(pairs)
    if trailing is not None:
        shots.append((trailing, None))

    frame_paths = sorted(raw_dir.glob(f"*.{extension}"))
    frame_times_arr = np.asarray(frame_times, dtype=np.float64)

    saved: list[Path] = []
    if not shots:
        logger.info("save_shot_images: no shots detected for %s", filename_stem)
        return saved

    with PoseDetector() as detector:
        for i, (shot_t, hit_t) in enumerate(shots, start=1):
            if len(frame_times_arr) == 0 or len(frame_paths) != len(frame_times_arr):
                logger.warning("save_shot_images: frame_times/frame_paths mismatch, skipping shot %d", i)
                continue
            nearest_idx = int(np.argmin(np.abs(frame_times_arr - shot_t)))
            frame = cv2.imread(str(frame_paths[nearest_idx]))
            if frame is None:
                continue

            boxes = detector.detect(frame, i)
            draw_palm_boxes(frame, boxes)

            if hit_t is not None:
                speed = puck_speed_kmh(shot_t, hit_t)
                if speed is not None:
                    _draw_bottom_centered_label(frame, f"{round(speed)} km/h", SHOT_IMAGE_FONT_PX, SHOT_IMAGE_LABEL_COLOR_BGR)

            out_path = out_dir / f"{filename_stem}-shot-{i:02d}.jpg"
            cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, SHOT_IMAGE_JPEG_QUALITY])
            saved.append(out_path)

    logger.info("save_shot_images: saved %d shot image(s) for %s", len(saved), filename_stem)
    return saved
