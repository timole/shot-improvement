"""Times a camera+mic recording, annotates it with palm boxes, and saves
raw + annotated clips (with audio) into recordings/.

Memory-conscious by design (this laptop has ~4GB RAM total): frames are
streamed straight to disk as PNGs as they're captured, never buffered
as a list. The only per-clip in-memory buffer is the audio array, which
even at 10s/44.1kHz/mono/int16 is under 1MB.

Frames are written as a PNG sequence rather than through
cv2.VideoWriter because the real per-frame throughput (camera decode +
pose inference) isn't known before the loop starts, and can't keep up
with the camera's own nominal fps on this modest hardware (~8fps
measured, not 30). A video container's own declared frame rate can't
be corrected after the fact - ffmpeg's "-r" as an input flag is
silently ignored for an already-timestamped container (verified: it
produced a 0.77s clip from a 3s recording) - so encoding starts from a
raw, untimed image sequence either way.

Spec 097 (audio/video sync fix): encoding that sequence with a single
constant "-framerate <average_fps>" (the previous approach here)
silently assumes every frame was captured at even intervals, which is
false on this hardware - a real 5s/149-frame test recording measured
per-frame gaps from 0ms to 375ms (mean 34ms, std 38ms), a single-camera
stall-then-burst pattern typical of DirectShow/USB capture under load.
Treating that as evenly spaced shifted a frame's displayed time in the
output by up to ~900ms from when it was really captured (confirmed by
extracting frames at a known instant and diffing against the raw
capture at that real timestamp - see specs/097). Since the audio track
has no equivalent distortion (a continuously-sampled WAV, not
frame-quantized), this uniform-average-fps encoding was long enough to
be the audio/video desync users could actually see.

encode_frames_with_audio() now takes each frame's REAL per-frame
capture timestamp (frame_times, tracked by the capture loops below)
and builds an ffmpeg concat-demuxer (ffconcat) input where each frame
carries its own measured on-screen duration, instead of a single
"-framerate" image2 input. "-fps_mode cfr" then resamples that
correctly-timed sequence onto a normal constant-rate output stream (so
browsers/players still get steady, seekable playback) by
duplicating/dropping frames as needed to match their real timestamps -
not by re-assuming even spacing. Measured fix: the same 900ms-class
error dropped to ~16ms (one camera frame's worth of sampling
granularity) on the same test recording.
"""

from __future__ import annotations

import ctypes
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import imageio_ffmpeg
import sounddevice as sd
import soundfile as sf

from . import devices
from .compose import compose_annotated_frames, save_shot_images
from .log_setup import get_logger
from .profiling import NULL_PROFILER, Profiler, dir_size

logger = get_logger("recorder")

PREFERRED_VIDEO_LABEL = "c922"
# Spec 091: the C922's own built-in mic is the default input now (not
# Jabra) - picked by the user as the default recording mic. Output
# (playback) stays on the Jabra speaker; separate constants since
# there's no reason those two should be forced to match.
PREFERRED_MIC_LABEL = "c922"
PREFERRED_SPEAKER_LABEL = "jabra"
SAMPLE_RATE = 44100
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
# Requested as a ceiling when opening a camera - cv2/DirectShow negotiates
# down to whatever the device actually supports; read back afterward
# (cap.get(cv2.CAP_PROP_FPS)) for the real value, never assumed.
REQUESTED_FPS_CEILING = 60.0
# Spec 086 found real captured fps stuck around 8-9fps and blamed this
# machine's CPU. That was wrong on two counts, both found and fixed in
# spec 088: (1) PREFERRED_VIDEO_LABEL="logitech" never actually matched
# this camera - Windows reports it as "c922 Pro Stream Webcam", with no
# "logitech" substring - so every prior recording silently fell back to
# a different, weaker "USB Camera" device, not the one OBS was tested
# against; (2) even on the right device, cv2/DirectShow defaults to
# uncompressed YUY2, which at 1280x720@60fps needs ~110MB/s - far past
# USB2's real throughput - so the pixel format itself capped fps long
# before the CPU was ever the bottleneck. Requesting MJPG (compressed,
# same as OBS) fixes this, but only when set AFTER width/height/fps -
# see open_camera().
REQUESTED_FOURCC = "MJPG"
# Spec 109: the C922's own auto-exposure was observed lengthening its
# per-frame exposure time under normal indoor lighting, which caps the
# real deliverable frame rate well below the negotiated 60fps/MJPG
# capability - measured on a real session: throughput dropped from
# ~32fps to ~10-14fps mid-session with no camera reopen in between (so
# not a negotiation/bandwidth issue - see REQUESTED_FOURCC above,
# already fixed in spec 088), and stayed there. Forcing a short, fixed
# exposure keeps the frame rate up regardless of ambient light, at the
# cost of a darker/possibly-blurrier image - an accepted tradeoff here,
# since this app's whole point is measuring fast motion, not a good
# picture. DirectShow's CAP_PROP_AUTO_EXPOSURE convention (not the
# more common V4L2 one): 0.25 = manual, 0.75 = auto.
MANUAL_EXPOSURE_MODE_DSHOW = 0.25
# DirectShow exposure is log2-scale seconds (value v -> 2**v seconds) -
# -7 is ~1/128s (~7.8ms), comfortably under the ~16.7ms one frame gets
# at 60fps. Camera/driver-specific; revisit if a real recording still
# doesn't reach 60fps with this set (see camera_info() / the "Opened
# camera" log line for the real negotiated exposure).
FIXED_EXPOSURE_DSHOW = -7
# Intermediate per-frame files (deleted once ffmpeg encodes the real
# output) are BMP, not PNG: measured on this hardware, PNG compression
# cost ~26ms/frame (two files per captured frame = ~53ms/frame) versus
# ~3ms/frame for BMP - PNG compression was a bigger cost than pose
# inference itself (~57ms/frame), and this app is throughput-bound on
# a weak CPU, so it's not worth paying for compression nothing else
# ever needs (the final .mp4 is still properly h264-compressed).
FRAME_FILE_EXTENSION = "bmp"

RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "recordings"

THREAD_PRIORITY_BELOW_NORMAL = -1


def lower_current_thread_priority() -> None:
    """Windows-only best-effort: demotes this thread's OS scheduling
    priority (spec 091). A new recording is now allowed to start while
    a previous one's background annotate/spectrogram/encode/cloud-sync
    is still running (see core.session.LiveSession.start_recording's
    is_recording-only gate) - this makes that prioritization concrete
    rather than hoping the GIL happens to interleave favorably: the
    live capture thread (left at normal priority) gets preferred CPU
    time over a background worker thread that calls this first, under
    contention on this machine's weak CPU. Never allowed to break the
    caller if it fails for any reason."""
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
    except Exception:
        logger.warning("lower_current_thread_priority: failed", exc_info=True)


@dataclass
class RecordingResult:
    raw_path: Path
    annotated_path: Path
    video_device_name: str
    audio_device_name: str | None
    frame_count: int
    actual_fps: float


def timestamp_for_filename(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def pick_video_device() -> tuple[int, str]:
    names = devices.list_video_devices()
    logger.debug("Enumerated video devices: %r", names)
    if not names:
        raise RuntimeError("Yhtään videolaitetta ei löytynyt.")
    idx = devices.find_video_device_index(PREFERRED_VIDEO_LABEL, names)
    matched_by_name = idx is not None
    if idx is None:
        idx = devices.first_non_virtual_video_device_index(names)
    logger.info(
        "Picked video device %d: %r (%s)", idx, names[idx],
        "name match" if matched_by_name else "fallback: first non-virtual",
    )
    return idx, names[idx]


def pick_audio_device() -> tuple[int | None, str | None]:
    idx = devices.find_audio_device_index(PREFERRED_MIC_LABEL)
    if idx is None:
        logger.info("Picked audio input: system default (no %r match found)", PREFERRED_MIC_LABEL)
        return None, None  # None -> sounddevice's system default input device
    name = sd.query_devices()[idx]["name"]
    logger.info("Picked audio input %d: %r (name match)", idx, name)
    return idx, name


def pick_output_audio_device() -> tuple[int | None, str | None]:
    idx = devices.find_output_audio_device_index(PREFERRED_SPEAKER_LABEL)
    if idx is None:
        logger.info("Picked audio output: system default (no %r match found)", PREFERRED_SPEAKER_LABEL)
        return None, None  # None -> sounddevice's system default output device
    name = sd.query_devices()[idx]["name"]
    logger.info("Picked audio output %d: %r (name match)", idx, name)
    return idx, name


def open_camera(index: int) -> cv2.VideoCapture:
    """Opens a camera requesting the highest fps it will negotiate to,
    at this app's fixed capture resolution. Reads one throwaway frame
    before returning - some DirectShow devices don't settle their real
    negotiated properties (what cap.get(...) reports) until the first
    frame has actually been pulled, so callers reading cap.get(...)
    immediately after this returns get accurate values, not stale ones
    left over from what was requested."""
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, REQUESTED_FPS_CEILING)
    # Must be set AFTER width/height/fps, not before - measured directly
    # on this hardware: setting FOURCC first has cap.set() report success
    # but cap.get(CAP_PROP_FOURCC) silently stays YUY2 and real fps caps
    # around 10; setting it last actually negotiates MJPG and gets ~58
    # real fps at 1280x720.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*REQUESTED_FOURCC))
    # Spec 109: forces a short, fixed exposure - see MANUAL_EXPOSURE_
    # MODE_DSHOW/FIXED_EXPOSURE_DSHOW's own docstrings for why. Applied
    # last, same reasoning as FOURCC above (a property set before the
    # capture mode settles has been unreliable on this hardware).
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, MANUAL_EXPOSURE_MODE_DSHOW)
    cap.set(cv2.CAP_PROP_EXPOSURE, FIXED_EXPOSURE_DSHOW)
    if cap.isOpened():
        cap.read()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4))
        exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
        logger.info(
            "Opened camera %d: negotiated %dx%d @ %.1f fps, fourcc=%r, exposure=%.1f "
            "(requested ceiling %.0f, fourcc %r, exposure %.1f)",
            index, width, height, fps, fourcc_str, exposure,
            REQUESTED_FPS_CEILING, REQUESTED_FOURCC, FIXED_EXPOSURE_DSHOW,
        )
    else:
        logger.warning("Failed to open camera %d", index)
    return cap


def _write_concat_list(frames_dir: Path, frame_times: Sequence[float]) -> Path:
    """Writes an ffconcat file (ffmpeg's concat demuxer, "ffconcat
    version 1.0") giving each captured frame its REAL measured
    on-screen duration - the gap to the next frame's real capture time
    - instead of the single constant "-framerate" the image2 demuxer
    would otherwise force onto every frame. See this module's docstring
    (spec 097) for why that distinction matters here.

    The concat demuxer documents that the FINAL "duration" directive is
    ignored (there's no next file to end the last one's display at) -
    the standard workaround, used here, is to repeat the last file once
    more as a trailing entry with no duration of its own."""
    list_path = frames_dir / "concat_list.txt"
    n = len(frame_times)
    lines = ["ffconcat version 1.0"]
    for i in range(n):
        name = f"frame_{i:06d}.{FRAME_FILE_EXTENSION}"
        if i + 1 < n:
            duration = max(frame_times[i + 1] - frame_times[i], 0.001)
        elif n > 1:
            duration = max(frame_times[i] - frame_times[i - 1], 0.001)
        else:
            duration = 1.0
        lines.append(f"file '{name}'")
        lines.append(f"duration {duration:.6f}")
    lines.append(f"file 'frame_{n - 1:06d}.{FRAME_FILE_EXTENSION}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def encode_frames_with_audio(
    frames_dir: Path,
    audio_path: Path,
    out_path: Path,
    actual_fps: float,
    frame_count: int,
    frame_times: Optional[Sequence[float]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    profiler: Profiler = NULL_PROFILER,
) -> None:
    """frame_times (spec 097), if given, is each frame's real
    time.monotonic()-based capture timestamp (seconds from when capture
    started) - used to build a correctly-timed concat-demuxer input
    instead of assuming evenly-spaced frames (see module docstring).
    Falls back to the old constant-"-framerate" image2 input when not
    given or when its length doesn't match frame_count (e.g. a caller
    that genuinely has no per-frame timing - shouldn't happen from
    anything in this app today, all of which tracks it).

    on_progress(done, total), if given, is called as ffmpeg reports
    its own encoded frame count (spec 090) - `-progress pipe:1` writes
    machine-readable `frame=N` lines to stdout; `-nostats` turns off
    the human-readable per-frame line ffmpeg would otherwise write to
    stderr instead (redundant here, and stderr is reserved below for
    real error output only). `-stats_period 0.1` asks for updates every
    100ms rather than the 0.5s default - this app's clips are only a
    few seconds long, so the default cadence would give very few
    updates to show.

    Deliberately not `-loglevel error` (quiets stdout too on some
    ffmpeg builds' `-progress` interaction) - default verbosity's
    banner + libx264 param dump can be a few KB on stderr, comfortably
    past a pipe's OS buffer (observed hanging both encode calls, since
    this app runs two per recording, back to back) if nothing drains it
    while this function is busy reading stdout instead - a dedicated
    thread below drains stderr into a bounded buffer concurrently, so
    ffmpeg is never left blocked trying to write to a full pipe while
    this function waits on stdout.

    profiler (spec 092), if given, times the whole call under
    "encode:<out_path name>". ffmpeg is a child process, so this
    process's own I/O counters can't see its reads/writes (see
    core.profiling's module docstring) - reported instead as the
    frames_dir size (read, resolved at entry - doesn't change during
    encoding) and out_path's size (write, resolved at exit, once
    ffmpeg has actually written it)."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    if frame_times is not None and len(frame_times) == frame_count and frame_count > 0:
        concat_list = _write_concat_list(frames_dir, frame_times)
        video_input_args = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
        # Resamples the real (variable) per-frame timing above onto a
        # normal constant-rate output (steady, seekable playback) by
        # duplicating/dropping frames to match their true timestamps -
        # not by re-assuming even spacing.
        video_output_args = ["-fps_mode", "cfr", "-r", f"{actual_fps:.3f}"]
    else:
        video_input_args = ["-framerate", f"{actual_fps:.3f}", "-i", str(frames_dir / f"frame_%06d.{FRAME_FILE_EXTENSION}")]
        video_output_args = []
    cmd = [
        ffmpeg_exe, "-y",
        *video_input_args,
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        *video_output_args,
        "-c:a", "aac",
        "-shortest",
        "-nostats",
        "-progress", "pipe:1",
        "-stats_period", "0.1",
        str(out_path),
    ]
    with profiler.stage(
        f"encode:{out_path.name}",
        extra_read_bytes=lambda: dir_size(frames_dir)[0],
        extra_write_bytes=lambda: out_path.stat().st_size,
    ):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # BELOW_NORMAL_PRIORITY_CLASS (spec 091): ffmpeg's own CPU usage
            # (libx264 encoding), not the calling Python thread, is the real
            # cost here - this lets a newer recording's live capture
            # preferentially get CPU time from Windows' own scheduler while
            # an older clip is still encoding in the background.
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
        )
        stderr_chunks: list[str] = []

        def drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_chunks.append(line)

        stderr_thread = threading.Thread(target=drain_stderr, name="ffmpeg-stderr-drain", daemon=True)
        stderr_thread.start()

        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("frame=") and on_progress and frame_count > 0:
                try:
                    frame_n = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                on_progress(min(frame_n, frame_count), frame_count)
        proc.wait()
        stderr_thread.join()
        stderr_text = "".join(stderr_chunks)
        if proc.returncode != 0:
            logger.error("ffmpeg encode of %s failed (exit %d): %s", out_path.name, proc.returncode, stderr_text[-2000:])
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=None, stderr=stderr_text)
        if on_progress and frame_count > 0:
            on_progress(frame_count, frame_count)
        logger.info("Encoded %s (%.1f fps, %d bytes)", out_path.name, actual_fps, out_path.stat().st_size)


def record_clip(
    duration_s: float = 3.0,
    out_dir: Path = RECORDINGS_DIR,
    on_status=lambda message: None,
    # (stage_label, fraction 0..1), called repeatedly during each of the
    # long post-capture steps below (spec 090) - separate from on_status
    # since those are one-off lines and this fires many times per stage.
    on_progress: Callable[[str, float], None] = lambda stage, fraction: None,
    profiler: Profiler = NULL_PROFILER,
) -> RecordingResult:
    logger.info("record_clip: starting, requested duration=%.1fs", duration_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    video_index, video_name = pick_video_device()
    audio_index, audio_name = pick_audio_device()
    on_status(f"Videolaite: {video_name}")
    on_status(f"Äänilaite: {audio_name or '(järjestelmän oletus)'}")

    cap = open_camera(video_index)
    if not cap.isOpened():
        logger.error("record_clip: camera %d (%r) failed to open", video_index, video_name)
        raise RuntimeError(f"Kameraa '{video_name}' ei saatu auki.")
    on_status(f"Resoluutio: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}, {cap.get(cv2.CAP_PROP_FPS):.1f} fps")
    _fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
    _fourcc_str = "".join(chr((_fourcc_int >> (8 * i)) & 0xFF) for i in range(4))
    profiler.note("video_device", f"{video_index}:{video_name}")
    profiler.note("audio_device", audio_name or "(system default)")
    profiler.note("negotiated_fourcc", _fourcc_str)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        raw_frames_dir = tmp_path / "raw"
        annotated_frames_dir = tmp_path / "annotated"
        raw_frames_dir.mkdir()
        annotated_frames_dir.mkdir()
        audio_tmp = tmp_path / "audio.wav"
        profiler.set_temp_dir(tmp_path)

        audio_buffer = sd.rec(
            int(duration_s * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=audio_index,
        )

        on_status(f"Nauhoitetaan {duration_s:.1f} sekuntia…")
        frame_count = 0
        failed_reads = 0
        frame_times: list[float] = []
        # Spec 086: pose inference (~57ms/frame, measured) used to run
        # inside this loop on every frame - it was the single biggest
        # cost limiting real captured fps, which mattered enough to
        # actually lose fast stick-bend motion between frames. Capture
        # now only reads + writes the raw frame; annotate_frames_dir()
        # runs pose detection afterward, once, over the saved files -
        # see its docstring for the measured effect.
        loop_start = time.monotonic()
        with profiler.stage("capture"):
            while time.monotonic() - loop_start < duration_s:
                with profiler.accum("capture:cap.read"):
                    ok, frame = cap.read()
                if not ok:
                    failed_reads += 1
                    if failed_reads in (1, 10, 50) or failed_reads % 100 == 0:
                        logger.warning("record_clip: camera read failed (count=%d)", failed_reads)
                    continue
                with profiler.accum("capture:imwrite"):
                    cv2.imwrite(str(raw_frames_dir / f"frame_{frame_count:06d}.{FRAME_FILE_EXTENSION}"), frame)
                frame_times.append(time.monotonic() - loop_start)
                frame_count += 1
        elapsed_s = time.monotonic() - loop_start

        with profiler.stage("audio:wait+write"):
            sd.wait()
            cap.release()
            sf.write(audio_tmp, audio_buffer, SAMPLE_RATE)

        if frame_count == 0:
            logger.error("record_clip: zero frames captured (failed_reads=%d)", failed_reads)
            raise RuntimeError("Kamerasta ei saatu yhtään kuvaa nauhoituksen aikana.")
        actual_fps = frame_count / elapsed_s if elapsed_s > 0 else 1.0
        on_status(f"Kuvia: {frame_count} ({actual_fps:.1f} fps toteutunut)")
        logger.info("record_clip: captured %d frames in %.2fs (%.1f fps, %d failed reads)", frame_count, elapsed_s, actual_fps, failed_reads)

        timestamp = timestamp_for_filename(datetime.now())
        raw_out = out_dir / f"shot-improvement-{timestamp}.mp4"
        annotated_out = out_dir / f"shot-improvement-{timestamp}-annotated.mp4"
        on_status("Tunnistetaan käsien asentoja ja lisätään spektrogrammi…")
        # Spec 092: fused single pass - was annotate_frames_dir() then
        # add_spectrograms_to_frames() (kept in core/pose.py and
        # core/spectrogram.py, unused here now, as the pixel-identity
        # reference for compose_annotated_frames - see its docstring).
        # Spec 085: only the annotated clip grows a spectrogram+playhead
        # panel underneath - the raw clip stays at the camera's native
        # resolution. Done here, after capture, since the full audio
        # buffer (needed for the whole-clip spectrogram) only exists
        # once sd.rec() has finished.
        compose_annotated_frames(
            raw_frames_dir, annotated_frames_dir, FRAME_FILE_EXTENSION, actual_fps,
            audio_buffer, frame_count, FRAME_WIDTH, FRAME_HEIGHT,
            frame_times=frame_times,
            on_progress=lambda done, total: on_progress("Tunnistetaan käsien asentoja ja spektrogrammi", done / total if total else 1.0),
            profiler=profiler,
        )
        # Spec 099: a plain snapshot + speed label per detected shot,
        # alongside the raw/annotated mp4s - reuses the same raw frames
        # (still on disk, not yet cleaned up) and re-runs clap detection
        # on the same audio (cheap, ~12ms - see spec 098's profiling).
        with profiler.accum("shot_images"):
            save_shot_images(
                raw_frames_dir, FRAME_FILE_EXTENSION, frame_times, audio_buffer, SAMPLE_RATE,
                out_dir, f"shot-improvement-{timestamp}",
            )
        on_status("Yhdistetään ääni ja kuva…")
        encode_frames_with_audio(
            raw_frames_dir, audio_tmp, raw_out, actual_fps, frame_count,
            frame_times=frame_times,
            on_progress=lambda done, total: on_progress("Tallennetaan levylle (raaka)", done / total if total else 1.0),
            profiler=profiler,
        )
        encode_frames_with_audio(
            annotated_frames_dir, audio_tmp, annotated_out, actual_fps, frame_count,
            frame_times=frame_times,
            on_progress=lambda done, total: on_progress("Tallennetaan levylle", done / total if total else 1.0),
            profiler=profiler,
        )
        profiler.note("frame_count", frame_count)
        profiler.note("actual_fps", round(actual_fps, 2))
        profiler.note("failed_reads", failed_reads)
        profiler.note("raw_out_bytes", raw_out.stat().st_size)
        profiler.note("annotated_out_bytes", annotated_out.stat().st_size)

    logger.info("record_clip: done -> %s / %s", raw_out.name, annotated_out.name)
    return RecordingResult(raw_out, annotated_out, video_name, audio_name, frame_count, actual_fps)
