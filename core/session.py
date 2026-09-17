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

from .compose import compose_annotated_frames, save_shot_images
from .log_setup import get_logger
from .pending import PENDING_DIR, delete_pending, write_meta
from .pose import PoseDetector, draw_palm_boxes
from .profiling import Profiler
from .recorder import (
    FRAME_FILE_EXTENSION,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    SAMPLE_RATE,
    encode_frames_with_audio,
    lower_current_thread_priority,
    open_camera,
    pick_audio_device,
    pick_output_audio_device,
    pick_video_device,
    timestamp_for_filename,
)

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
        # Spec 097: each frame's real time.monotonic() capture timestamp
        # (seconds from self._record_start) - see core.recorder's module
        # docstring for why encoding needs this instead of assuming
        # frames were captured at even intervals.
        self._frame_times: list[float] = []
        self._tmp_dir: Optional[tempfile.TemporaryDirectory] = None
        self._raw_dir: Optional[Path] = None
        self._annotated_dir: Optional[Path] = None
        self._out_dir: Optional[Path] = None
        self._audio_buffer: Optional[np.ndarray] = None
        self._on_recording_done: Optional[Callable[[Optional[RecordingResult]], None]] = None
        self._consecutive_read_failures = 0
        # Spec 091: a count, not a bool - a new recording can now start
        # (see start_recording()) while an older one's background worker
        # is still running, so more than one can be in flight at once.
        # is_busy must stay True until the LAST of them finishes, not
        # whichever happens to finish first.
        self._encoding_count = 0
        self._dispatch: Callable[[Callable[[], None]], None] = lambda fn: fn()
        self._on_progress: Callable[[str, float], None] = lambda stage, fraction: None
        self._on_raw_ready: Callable[[Path], None] = lambda raw_path: None
        # Real Profiler() (spec 092) is (re)created per recording in
        # start_recording, once its temp dir exists; NULL_PROFILER here
        # is only a safe default for the sliver of time before a first
        # recording starts.
        self._profiler = Profiler(enabled=False)
        # Spec 093: "Vain nauhoitus" (recording only) mode - set per
        # recording in start_recording(). When True, _start_finish_
        # recording() skips all processing (encode/compose/encode) and
        # just persists raw frames + audio to a durable pending/ entry
        # instead of an ephemeral temp dir, for
        # core.pending.process_pending_recording() to pick up later.
        self._defer_processing = False
        self._timestamp = ""
        self._pending_capture_dir: Optional[Path] = None
        self._on_deferred_saved: Callable[[bool], None] = lambda ok: None

        logger.info(
            "LiveSession started: video=%r audio=%r output=%r",
            self.video_name, self.audio_name, self.output_name,
        )

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_busy(self) -> bool:
        """True while actively capturing OR while at least one previous
        clip is still encoding in the background (spec 091: more than
        one can be in flight - see _encoding_count). Used to gate
        actions that shouldn't run concurrently with any of that (e.g.
        playback, camera close) - starting a NEW recording is
        deliberately not gated on this, see start_recording()."""
        return self._recording or self._encoding_count > 0

    def set_profiler(self, profiler: Profiler) -> None:
        """Spec 092: lets a caller (tools/bench_live.py) enable
        profiling for read_frame()'s live-preview branch (cap.read /
        detect / draw), outside of any recording - start_recording()
        otherwise creates its own Profiler per clip and this gets
        overwritten at that point, which is intended (each recording's
        report should reflect just that recording)."""
        self._profiler = profiler

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
        on_progress: Callable[[str, float], None] = lambda stage, fraction: None,
        on_raw_ready: Callable[[Path], None] = lambda raw_path: None,
        defer_processing: bool = False,
        on_deferred_saved: Callable[[bool], None] = lambda ok: None,
    ) -> None:
        """dispatch, if given, is used to run on_done back on whatever
        thread called start_recording (e.g. Tkinter's root.after(0, fn))
        - encoding happens on a background thread (see _finish_recording),
        so on_done must never touch GUI widgets directly from there.

        on_progress(stage_label, fraction), if given, is called (also via
        dispatch) many times during the background worker's post-capture
        steps (spec 090) - annotating, adding the spectrogram, and each
        of the two ffmpeg encodes.

        on_raw_ready(raw_path), if given, is called (also via dispatch)
        once the RAW clip alone has finished encoding - well before the
        rest of the pipeline (annotate/spectrogram/encode the annotated
        copy/cloud sync) completes (spec 091). The GUI uses this to show
        the new recording in its list and re-enable the record button
        immediately, rather than waiting for on_done.

        defer_processing (spec 093): when True, this recording's frames
        are captured exactly as normal, but NO processing (pose
        annotation, spectrogram, ffmpeg encoding) runs afterward at all
        - they're saved to core.pending.PENDING_DIR instead of an
        ephemeral temp dir, and on_deferred_saved(ok) is called (also
        via dispatch) once that save completes, INSTEAD of on_raw_ready/
        on_done (neither fires in this mode - there is no mp4 yet).
        Call core.pending.process_pending_recording() later (the GUI's
        "Käsittele odottavat" button) to actually turn saved frames into
        mp4s. Exists because post-capture processing is CPU-heavy enough
        on this hardware to visibly compete with a NEW recording's own
        capture even with spec 091's priority handling - this mode lets
        the user skip that contention entirely by choosing when the
        heavy work runs.

        Gated on is_recording, not is_busy: a previous clip's background
        work (annotate/spectrogram/encode/sync, or a pending-queue being
        processed) is deliberately allowed to still be running when this
        is called - see _start_finish_recording's
        _lower_current_thread_priority() call, which exists specifically
        so that background work doesn't starve THIS new capture of CPU.
        Only actual capture blocks a new one."""
        if self._recording:
            return
        self._dispatch = dispatch
        self._on_progress = on_progress
        self._on_raw_ready = on_raw_ready
        self._on_deferred_saved = on_deferred_saved
        self._defer_processing = defer_processing
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
        # was reliable in every test). Unnecessary in defer_processing
        # mode (no detection ever runs against this recording's own
        # frames here) but kept unconditional - it's cheap, and the
        # detector is needed again the moment live preview resumes
        # regardless of mode.
        self.detector.close()
        self.detector = PoseDetector()

        # Spec 093: timestamp assigned once, up front, rather than at
        # encode-worker time as before - the only place it's used is
        # the output filename, and fixing it here means that filename
        # reflects when the clip was actually recorded even if
        # background processing (this clip's own, or an older one's)
        # doesn't start for a while under load. Also doubles as the
        # pending/ directory name in defer_processing mode.
        self._timestamp = timestamp_for_filename(datetime.now())
        self._profiler = Profiler()
        if defer_processing:
            # Straight to durable storage, no temp dir at all - frames
            # must survive past this call (see docstring above).
            capture_dir = PENDING_DIR / self._timestamp
            self._raw_dir = capture_dir / "raw"
            self._raw_dir.mkdir(parents=True)
            self._annotated_dir = None
            self._tmp_dir = None
            self._pending_capture_dir = capture_dir
            self._profiler.set_temp_dir(capture_dir)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            self._tmp_dir = tempfile.TemporaryDirectory()
            tmp_path = Path(self._tmp_dir.name)
            self._raw_dir = tmp_path / "raw"
            self._annotated_dir = tmp_path / "annotated"
            self._raw_dir.mkdir()
            self._annotated_dir.mkdir()
            self._pending_capture_dir = None
            # Spec 092: one Profiler per recording, off unless
            # SHOT_IMPROVEMENT_PROFILE=1 (Profiler()'s own default) -
            # shared by read_frame()'s capture-side accum() calls below
            # and by the worker's annotate/spectrogram/encode stage()
            # calls, so a single before/after report covers the whole
            # GUI recording, same instrumentation as core.recorder.
            # record_clip's CLI path.
            self._profiler.set_temp_dir(tmp_path)
        self._out_dir = out_dir

        self._audio_buffer = sd.rec(
            int(duration_s * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=self.audio_index,
        )

        self._record_duration = duration_s
        self._record_start = time.monotonic()
        self._record_frame_count = 0
        self._frame_times = []
        self._on_recording_done = on_done
        self._recording = True
        logger.info(
            "start_recording: duration=%.1fs audio_device=%r defer_processing=%s",
            duration_s, self.audio_name, defer_processing,
        )

    def read_frame(self) -> Optional[np.ndarray]:
        """Reads and returns one annotated BGR frame (or None if the
        camera read failed). Also streams to disk, and finalizes the
        clip via ffmpeg once the requested duration has elapsed, as a
        side effect, whenever a recording is in progress - so the
        caller just needs to keep calling this in a loop."""
        frame_start = time.monotonic()
        with self._profiler.accum("capture:cap.read"):
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
            with self._profiler.accum("capture:imwrite"):
                cv2.imwrite(str(self._raw_dir / f"frame_{self._record_frame_count:06d}.{FRAME_FILE_EXTENSION}"), frame)
            self._frame_times.append(time.monotonic() - self._record_start)
            self._record_frame_count += 1
            if time.monotonic() - self._record_start >= self._record_duration:
                self._start_finish_recording()
            elapsed = time.monotonic() - frame_start
            if elapsed > SLOW_FRAME_WARN_THRESHOLD_S:
                logger.warning("read_frame: took %.2fs (recording=%s)", elapsed, self._recording)
            return frame

        ts_ms = int((time.monotonic() - self._session_start) * 1000)
        with self._profiler.accum("live:detect"):
            boxes = self.detector.detect(frame, ts_ms)
        with self._profiler.accum("live:draw"):
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
        # preview keep running at full speed, AND so a new recording is
        # free to start (see start_recording()'s gate). Everything else
        # (spec 093: either encoding, twice, or - in defer_processing
        # mode - just persisting raw frames) runs on a background
        # thread instead of blocking read_frame()'s caller: a real ~12s
        # camera stall was observed and confirmed via logging when this
        # ran synchronously on the GUI's own tick loop.
        #
        # Spec 091: a NEW recording is now allowed to start while this
        # worker is still running (is_busy is a count, not a bool - see
        # _encoding_count) - unlike before, this is deliberate: the user
        # asked for a new recording to be prioritized over an older one's
        # background work, not blocked behind it. Both finisher methods
        # below lower their own thread priority (see
        # lower_current_thread_priority) so that prioritization is real,
        # not just hopeful GIL interleaving.
        self._recording = False
        self._encoding_count += 1
        elapsed_s = time.monotonic() - self._record_start
        frame_count = self._record_frame_count
        frame_times = self._frame_times
        audio_buffer = self._audio_buffer
        dispatch = self._dispatch
        timestamp = self._timestamp
        if self._defer_processing:
            self._finish_deferred_recording(elapsed_s, frame_count, frame_times, audio_buffer, dispatch)
        else:
            self._finish_immediate_recording(elapsed_s, frame_count, frame_times, audio_buffer, dispatch, timestamp)

    def _finish_immediate_recording(
        self, elapsed_s: float, frame_count: int, frame_times: list[float], audio_buffer: np.ndarray,
        dispatch: Callable[[Callable[[], None]], None], timestamp: str,
    ) -> None:
        """The normal path: encode raw -> on_raw_ready -> compose
        annotated frames (spec 092) -> encode annotated, all on a
        background thread. See _finish_deferred_recording for the
        "Vain nauhoitus" (spec 093) alternative."""
        tmp_dir = self._tmp_dir
        raw_dir, annotated_dir, out_dir = self._raw_dir, self._annotated_dir, self._out_dir
        on_done = self._on_recording_done
        on_raw_ready = self._on_raw_ready
        on_progress = self._on_progress
        profiler = self._profiler
        logger.info("finish_recording: %d frames in %.2fs, encoding in background", frame_count, elapsed_s)

        def report(stage: str) -> Callable[[int, int], None]:
            # Dispatched (not called directly) - runs on the GUI thread,
            # same as on_done, since on_progress ends up touching a
            # Tkinter status label.
            return lambda done, total: dispatch(
                lambda: on_progress(stage, done / total if total else 1.0)
            )

        def worker() -> None:
            lower_current_thread_priority()
            sd.wait()
            result: Optional[RecordingResult] = None
            try:
                if frame_count == 0:
                    logger.warning("finish_recording: zero frames captured")
                else:
                    with profiler.stage("audio:write"):
                        audio_tmp = Path(tmp_dir.name) / "audio.wav"
                        sf.write(audio_tmp, audio_buffer, SAMPLE_RATE)
                    actual_fps = frame_count / elapsed_s if elapsed_s > 0 else 1.0
                    profiler.note("frame_count", frame_count)
                    profiler.note("actual_fps", round(actual_fps, 2))

                    raw_out = out_dir / f"shot-improvement-{timestamp}.mp4"
                    annotated_out = out_dir / f"shot-improvement-{timestamp}-annotated.mp4"
                    # Spec 091: the raw clip needs no pose annotation at
                    # all, so it's encoded FIRST and handed to
                    # on_raw_ready immediately - the GUI shows it in the
                    # recordings list and re-enables the record button
                    # right away, well before the slower annotate/
                    # spectrogram/encode-annotated/cloud-sync steps below
                    # even start.
                    encode_frames_with_audio(
                        raw_dir, audio_tmp, raw_out, actual_fps, frame_count,
                        frame_times=frame_times,
                        on_progress=report("Tallennetaan levylle (raaka)"),
                        profiler=profiler,
                    )
                    dispatch(lambda: on_raw_ready(raw_out))
                    # Spec 085: annotated-only - see core.spectrogram's
                    # module docstring for why this runs here
                    # (background encode thread) rather than during
                    # capture. Spec 086: pose inference deferred here
                    # too, same reasoning as core.pose.annotate_frames_
                    # dir's docstring - must run before the spectrogram
                    # is composited in, since that permanently changes
                    # the annotated frame's size.
                    # Spec 092: fused single pass - was
                    # annotate_frames_dir() then add_spectrograms_to_
                    # frames() (kept in core/pose.py and
                    # core/spectrogram.py, unused here now, as the
                    # pixel-identity reference for
                    # compose_annotated_frames - see its docstring).
                    compose_annotated_frames(
                        raw_dir, annotated_dir, FRAME_FILE_EXTENSION, actual_fps,
                        audio_buffer, frame_count, FRAME_WIDTH, FRAME_HEIGHT,
                        frame_times=frame_times,
                        on_progress=report("Tunnistetaan käsien asentoja ja spektrogrammi"),
                        profiler=profiler,
                    )
                    # Spec 099: a plain snapshot + speed label per
                    # detected shot, alongside the raw/annotated mp4s.
                    with profiler.accum("shot_images"):
                        save_shot_images(
                            raw_dir, FRAME_FILE_EXTENSION, frame_times, audio_buffer, SAMPLE_RATE,
                            out_dir, f"shot-improvement-{timestamp}",
                        )
                    encode_frames_with_audio(
                        annotated_dir, audio_tmp, annotated_out, actual_fps, frame_count,
                        frame_times=frame_times,
                        on_progress=report("Tallennetaan levylle"),
                        profiler=profiler,
                    )
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
                profiler.report("gui-record")
                tmp_dir.cleanup()
                self._encoding_count -= 1
                if on_done:
                    dispatch(lambda: on_done(result))

        threading.Thread(target=worker, name="shot-improvement-encode", daemon=True).start()

    def _finish_deferred_recording(
        self, elapsed_s: float, frame_count: int, frame_times: list[float], audio_buffer: np.ndarray,
        dispatch: Callable[[Callable[[], None]], None],
    ) -> None:
        """Spec 093: "Vain nauhoitus" mode - persists raw frames + audio
        to a durable pending/ entry and stops, with NO processing (pose
        annotation, spectrogram, ffmpeg encoding) run at all.
        core.pending.process_pending_recording(), triggered later by the
        user (the GUI's "Käsittele odottavat" button), turns it into
        mp4s on request."""
        pending_dir = self._pending_capture_dir
        raw_dir = self._raw_dir
        video_name, audio_name = self.video_name, self.audio_name
        duration_s = self._record_duration
        on_deferred_saved = self._on_deferred_saved
        logger.info(
            "finish_recording (deferred): %d frames in %.2fs, saving to %s (no processing)",
            frame_count, elapsed_s, pending_dir,
        )

        def worker() -> None:
            lower_current_thread_priority()
            sd.wait()
            ok = False
            try:
                if frame_count == 0:
                    logger.warning("finish_recording (deferred): zero frames captured, discarding")
                    delete_pending(pending_dir)
                else:
                    actual_fps = frame_count / elapsed_s if elapsed_s > 0 else 1.0
                    sf.write(pending_dir / "audio.wav", audio_buffer, SAMPLE_RATE)
                    write_meta(
                        pending_dir, frame_count=frame_count, actual_fps=actual_fps,
                        duration_s=duration_s, video_name=video_name, audio_name=audio_name,
                        frame_times=frame_times,
                    )
                    ok = True
                    logger.info(
                        "finish_recording (deferred): saved %d frames (%.1f fps) -> %s",
                        frame_count, actual_fps, pending_dir,
                    )
            except Exception:
                logger.exception("finish_recording (deferred): save failed for %s", raw_dir)
                ok = False
            finally:
                self._encoding_count -= 1
                dispatch(lambda: on_deferred_saved(ok))

        threading.Thread(target=worker, name="shot-improvement-defer-save", daemon=True).start()

    def close(self) -> None:
        logger.info("LiveSession closing")
        self.cap.release()
        self.detector.close()
