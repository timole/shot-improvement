"""A persistent camera+mic+pose-detector session, for the GUI.

Unlike core.recorder.record_clip() (which opens the camera fresh for
each CLI invocation and closes it again afterward), the GUI needs one
continuously-live annotated preview PLUS the ability to record a clip
without reopening the camera mid-session (DirectShow doesn't like a
device being opened twice at once). LiveSession owns one open camera
and one PoseDetector for as long as the GUI runs; read_frame() is
meant to be called repeatedly (e.g. from a Tkinter after() loop) and
always returns the live annotated frame to display, additionally
streaming frames to disk while a recording is in progress.
"""

from __future__ import annotations

import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
import sounddevice as sd
import soundfile as sf

from .log_setup import get_logger
from .pose import PoseDetector, annotate_frames_dir, draw_palm_boxes
from .recorder import (
    FRAME_FILE_EXTENSION,
    SAMPLE_RATE,
    encode_frames_with_audio,
    open_camera,
    pick_audio_device,
    pick_output_audio_device,
    pick_video_device,
    timestamp_for_filename,
)
from .spectrogram import add_spectrograms_to_frames

logger = get_logger("session")

# read_frame() logs a warning the 1st/10th/50th.../every-100th
# consecutive camera-read failure (not every single one - a genuinely
# stuck camera would otherwise flood the log with nothing new to say),
# and escalates to an error once it looks less like a hiccup and more
# like the camera is actually stuck.
CONSECUTIVE_FAILURE_WARN_AT = (1, 10, 50)
CONSECUTIVE_FAILURE_ERROR_AT = 200
# read_frame() is called every ~10ms from the GUI's Tkinter loop; one
# call taking much longer than the ~150ms a normal pose-inference tick
# costs on this hardware is worth knowing about (a real stutter, not
# just "inference is slow").
SLOW_FRAME_WARN_THRESHOLD_S = 1.0


@dataclass
class RecordingResult:
    raw_path: Path
    annotated_path: Path
    frame_count: int
    actual_fps: float


class LiveSession:
    def __init__(self) -> None:
        self.video_index, self.video_name = pick_video_device()
        self.audio_index, self.audio_name = pick_audio_device()
        self.output_index, self.output_name = pick_output_audio_device()

        self.cap = open_camera(self.video_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Kameraa '{self.video_name}' ei saatu auki.")

        self.detector = PoseDetector()
        self._session_start = time.monotonic()

        self._recording = False
        self._record_duration = 0.0
        self._record_start = 0.0
        self._record_frame_count = 0
        self._tmp_dir: Optional[tempfile.TemporaryDirectory] = None
        self._raw_dir: Optional[Path] = None
        self._annotated_dir: Optional[Path] = None
        self._out_dir: Optional[Path] = None
        self._audio_buffer: Optional[np.ndarray] = None
        self._on_recording_done: Optional[Callable[[Optional[RecordingResult]], None]] = None
        self._consecutive_read_failures = 0
        self._encoding = False
        self._dispatch: Callable[[Callable[[], None]], None] = lambda fn: fn()

        logger.info(
            "LiveSession started: video=%r audio=%r output=%r",
            self.video_name, self.audio_name, self.output_name,
        )

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_busy(self) -> bool:
        """True while actively capturing OR while a previous clip is
        still encoding in the background - starting a second recording
        while the first is still encoding would just make both compete
        for this machine's limited CPU. Camera switching etc. only care
        about is_recording (capture), not encoding."""
        return self._recording or self._encoding

    def camera_info(self) -> tuple[int, int, float]:
        """Real, negotiated (width, height, fps) - never assumed."""
        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        return width, height, fps

    def suspend_camera(self) -> None:
        """Fully releases the camera device rather than just not calling
        read() on it - some Windows capture backends (DirectShow/MSMF)
        keep a capture graph running its own thread in the background
        regardless of whether the app reads frames, which was suspected
        (not fully proven) to be starving CPU/GIL time from unrelated
        background work like BackgroundRemover model loading. Only safe
        to call while nothing needs the live feed (e.g. during playback,
        where the "Häivytä tausta" button lives) - see resume_camera()."""
        if self._recording:
            raise RuntimeError("Kameraa ei voi vapauttaa nauhoituksen aikana.")
        logger.info("suspend_camera: releasing %r", self.video_name)
        self.cap.release()

    def resume_camera(self) -> None:
        logger.info("resume_camera: reopening %r", self.video_name)
        self.cap = open_camera(self.video_index)

    def switch_video_device(self, index: int, name: str) -> None:
        if self._recording:
            raise RuntimeError("Kameraa ei voi vaihtaa nauhoituksen aikana.")
        before = self.camera_info()
        old_cap = self.cap
        new_cap = open_camera(index)
        if not new_cap.isOpened():
            new_cap.release()
            logger.warning("switch_video_device: failed to open %d (%r), keeping %r", index, name, self.video_name)
            raise RuntimeError(f"Kameraa '{name}' ei saatu auki.")
        self.cap = new_cap
        self.video_index, self.video_name = index, name
        old_cap.release()
        after = self.camera_info()
        logger.info("switch_video_device: %dx%d@%.1ffps -> %r %dx%d@%.1ffps", *before, name, *after)

    def set_audio_device(self, index: Optional[int], name: Optional[str]) -> None:
        logger.info("set_audio_device: %r -> %r", self.audio_name, name)
        self.audio_index, self.audio_name = index, name

    def set_output_device(self, index: Optional[int], name: Optional[str]) -> None:
        logger.info("set_output_device: %r -> %r", self.output_name, name)
        self.output_index, self.output_name = index, name

    def start_recording(
        self,
        duration_s: float,
        out_dir: Path,
        on_done: Callable[[Optional[RecordingResult]], None],
        dispatch: Callable[[Callable[[], None]], None] = lambda fn: fn(),
    ) -> None:
        """dispatch, if given, is used to run on_done back on whatever
        thread called start_recording (e.g. Tkinter's root.after(0, fn))
        - encoding happens on a background thread (see _finish_recording),
        so on_done must never touch GUI widgets directly from there."""
        if self.is_busy:
            return
        self._dispatch = dispatch
        # A PoseDetector reused continuously across a long idle-preview
        # session (minutes of frames, possibly a prior recording too)
        # was observed to silently stop detecting mid-recording, even
        # though the exact same frames re-processed through a fresh
        # detector afterward, standalone, detected correctly on every
        # one of them - confirmed by diffing a real recording's raw vs.
        # annotated output (a box appeared for the first ~2 frames, then
        # none for the rest). core.recorder.record_clip() never hit
        # this because it always creates a brand-new PoseDetector per
        # call; matching that pattern here for each recording avoids it
        # (the exact MediaPipe-internal mechanism wasn't pinned down,
        # but "fresh detector before recording" is the one pattern that
        # was reliable in every test).
        self.detector.close()
        self.detector = PoseDetector()
        out_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp_dir.name)
        self._raw_dir = tmp_path / "raw"
        self._annotated_dir = tmp_path / "annotated"
        self._raw_dir.mkdir()
        self._annotated_dir.mkdir()
        self._out_dir = out_dir

        self._audio_buffer = sd.rec(
            int(duration_s * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=self.audio_index,
        )

        self._record_duration = duration_s
        self._record_start = time.monotonic()
        self._record_frame_count = 0
        self._on_recording_done = on_done
        self._recording = True
        logger.info("start_recording: duration=%.1fs audio_device=%r", duration_s, self.audio_name)

    def read_frame(self) -> Optional[np.ndarray]:
        """Reads and returns one annotated BGR frame (or None if the
        camera read failed). Also streams to disk, and finalizes the
        clip via ffmpeg once the requested duration has elapsed, as a
        side effect, whenever a recording is in progress - so the
        caller just needs to keep calling this in a loop."""
        frame_start = time.monotonic()
        ok, frame = self.cap.read()
        if not ok:
            self._consecutive_read_failures += 1
            n = self._consecutive_read_failures
            if n in CONSECUTIVE_FAILURE_WARN_AT or n % 100 == 0:
                logger.warning("read_frame: camera read failed (consecutive=%d)", n)
            if n == CONSECUTIVE_FAILURE_ERROR_AT:
                logger.error("read_frame: camera appears stuck (%d consecutive failed reads)", n)
            return None
        if self._consecutive_read_failures:
            logger.info("read_frame: camera recovered after %d failed reads", self._consecutive_read_failures)
        self._consecutive_read_failures = 0

        if self._recording:
            # Spec 086: pose inference is NOT run here anymore - it's
            # deferred to a post-capture pass (annotate_frames_dir(),
            # called from the encode worker below) over the saved raw
            # frames. Measured ~57ms/frame, it was the single biggest
            # cost keeping real captured fps low, low enough that a
            # fast stick bend could be lost between frames entirely.
            # Skipping it here lets this loop do only a camera read + one
            # disk write per frame. The visible tradeoff: the live
            # preview shows undecorated frames for the duration of the
            # recording (no palm boxes) since drawing them would need
            # the same deferred inference - boxes reappear once the
            # clip is loaded back for playback, on the annotated file.
            cv2.imwrite(str(self._raw_dir / f"frame_{self._record_frame_count:06d}.{FRAME_FILE_EXTENSION}"), frame)
            self._record_frame_count += 1
            if time.monotonic() - self._record_start >= self._record_duration:
                self._start_finish_recording()
            elapsed = time.monotonic() - frame_start
            if elapsed > SLOW_FRAME_WARN_THRESHOLD_S:
                logger.warning("read_frame: took %.2fs (recording=%s)", elapsed, self._recording)
            return frame

        ts_ms = int((time.monotonic() - self._session_start) * 1000)
        boxes = self.detector.detect(frame, ts_ms)
        annotated = frame.copy()
        draw_palm_boxes(annotated, boxes)

        elapsed = time.monotonic() - frame_start
        if elapsed > SLOW_FRAME_WARN_THRESHOLD_S:
            logger.warning("read_frame: took %.2fs (recording=%s)", elapsed, self._recording)

        return annotated

    def _start_finish_recording(self) -> None:
        # Capture (this app's per-frame throughput bottleneck: pose
        # inference + disk writes) is done the moment this is called -
        # self._recording flips off immediately so the camera/live
        # preview keep running at full speed. Encoding (ffmpeg, twice)
        # runs on a background thread instead of blocking read_frame()'s
        # caller: a real ~12s camera stall was observed and confirmed
        # via logging when this ran synchronously on the GUI's own tick
        # loop - encoding two clips (bigger now that the default
        # duration is 10s, not 3s) starved the camera of CPU for that
        # whole time. self.is_busy stays true until encoding finishes,
        # so a second recording can't start and compete with it for
        # this machine's limited CPU.
        self._recording = False
        self._encoding = True
        elapsed_s = time.monotonic() - self._record_start
        frame_count = self._record_frame_count
        tmp_dir = self._tmp_dir
        raw_dir, annotated_dir, out_dir = self._raw_dir, self._annotated_dir, self._out_dir
        audio_buffer = self._audio_buffer
        on_done = self._on_recording_done
        dispatch = self._dispatch
        logger.info("finish_recording: %d frames in %.2fs, encoding in background", frame_count, elapsed_s)

        def worker() -> None:
            sd.wait()
            result: Optional[RecordingResult] = None
            try:
                if frame_count == 0:
                    logger.warning("finish_recording: zero frames captured")
                else:
                    audio_tmp = Path(tmp_dir.name) / "audio.wav"
                    sf.write(audio_tmp, audio_buffer, SAMPLE_RATE)
                    actual_fps = frame_count / elapsed_s if elapsed_s > 0 else 1.0

                    timestamp = timestamp_for_filename(datetime.now())
                    raw_out = out_dir / f"shot-improvement-{timestamp}.mp4"
                    annotated_out = out_dir / f"shot-improvement-{timestamp}-annotated.mp4"
                    # Spec 085: annotated-only, 640x480 -> 640x960 - see
                    # core.spectrogram's module docstring for why this
                    # runs here (background encode thread) rather than
                    # during capture.
                    # Spec 086: pose inference deferred here too, same
                    # reasoning as core.pose.annotate_frames_dir's
                    # docstring - must run before the spectrogram is
                    # composited in, since that permanently changes the
                    # annotated frame's size.
                    annotate_frames_dir(raw_dir, annotated_dir, FRAME_FILE_EXTENSION, actual_fps)
                    add_spectrograms_to_frames(annotated_dir, audio_buffer, frame_count, FRAME_FILE_EXTENSION)
                    encode_frames_with_audio(raw_dir, audio_tmp, raw_out, actual_fps)
                    encode_frames_with_audio(annotated_dir, audio_tmp, annotated_out, actual_fps)
                    result = RecordingResult(raw_out, annotated_out, frame_count, actual_fps)
                    logger.info("finish_recording: done -> %s / %s", raw_out.name, annotated_out.name)
            except Exception:
                # Never let this propagate uncaught - it's running on a
                # background thread, where an uncaught exception would
                # just silently kill the thread with nothing visible
                # anywhere except (now) the log.
                logger.exception("finish_recording: encode failed")
                result = None
            finally:
                tmp_dir.cleanup()
                self._encoding = False
                if on_done:
                    dispatch(lambda: on_done(result))

        threading.Thread(target=worker, name="shot-improvement-encode", daemon=True).start()

    def close(self) -> None:
        logger.info("LiveSession closing")
        self.cap.release()
        self.detector.close()
