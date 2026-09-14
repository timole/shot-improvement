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
produced a 0.77s clip from a 3s recording) - but an image2 sequence
carries no timing at all, so "-framerate <measured_fps>" at encode time
is exactly correct, computed as frame_count / real_elapsed_seconds
after the loop.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import imageio_ffmpeg
import sounddevice as sd
import soundfile as sf

from . import devices
from .log_setup import get_logger
from .pose import annotate_frames_dir
from .spectrogram import add_spectrograms_to_frames

logger = get_logger("recorder")

PREFERRED_VIDEO_LABEL = "c922"
PREFERRED_AUDIO_LABEL = "jabra"
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
# Intermediate per-frame files (deleted once ffmpeg encodes the real
# output) are BMP, not PNG: measured on this hardware, PNG compression
# cost ~26ms/frame (two files per captured frame = ~53ms/frame) versus
# ~3ms/frame for BMP - PNG compression was a bigger cost than pose
# inference itself (~57ms/frame), and this app is throughput-bound on
# a weak CPU, so it's not worth paying for compression nothing else
# ever needs (the final .mp4 is still properly h264-compressed).
FRAME_FILE_EXTENSION = "bmp"

RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "recordings"


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
    idx = devices.find_audio_device_index(PREFERRED_AUDIO_LABEL)
    if idx is None:
        logger.info("Picked audio input: system default (no %r match found)", PREFERRED_AUDIO_LABEL)
        return None, None  # None -> sounddevice's system default input device
    name = sd.query_devices()[idx]["name"]
    logger.info("Picked audio input %d: %r (name match)", idx, name)
    return idx, name


def pick_output_audio_device() -> tuple[int | None, str | None]:
    idx = devices.find_output_audio_device_index(PREFERRED_AUDIO_LABEL)
    if idx is None:
        logger.info("Picked audio output: system default (no %r match found)", PREFERRED_AUDIO_LABEL)
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
    if cap.isOpened():
        cap.read()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4))
        logger.info(
            "Opened camera %d: negotiated %dx%d @ %.1f fps, fourcc=%r (requested ceiling %.0f, fourcc %r)",
            index, width, height, fps, fourcc_str, REQUESTED_FPS_CEILING, REQUESTED_FOURCC,
        )
    else:
        logger.warning("Failed to open camera %d", index)
    return cap


def encode_frames_with_audio(frames_dir: Path, audio_path: Path, out_path: Path, actual_fps: float) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        subprocess.run(
            [
                ffmpeg_exe, "-y",
                "-framerate", f"{actual_fps:.3f}",
                "-i", str(frames_dir / f"frame_%06d.{FRAME_FILE_EXTENSION}"),
                "-i", str(audio_path),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-shortest",
                str(out_path),
            ],
            check=True,
            capture_output=True,
            # Without this, each ffmpeg invocation briefly flashes its
            # own console window - subprocess.run() on Windows opens one
            # by default for a console-subsystem child process, even
            # though this app itself runs windowless under pythonw.exe.
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg encode of %s failed (exit %d): %s", out_path.name, exc.returncode, exc.stderr.decode("utf-8", "replace")[-2000:])
        raise
    logger.info("Encoded %s (%.1f fps, %d bytes)", out_path.name, actual_fps, out_path.stat().st_size)


def record_clip(
    duration_s: float = 3.0,
    out_dir: Path = RECORDINGS_DIR,
    on_status=lambda message: None,
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

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        raw_frames_dir = tmp_path / "raw"
        annotated_frames_dir = tmp_path / "annotated"
        raw_frames_dir.mkdir()
        annotated_frames_dir.mkdir()
        audio_tmp = tmp_path / "audio.wav"

        audio_buffer = sd.rec(
            int(duration_s * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=audio_index,
        )

        on_status(f"Nauhoitetaan {duration_s:.1f} sekuntia…")
        frame_count = 0
        failed_reads = 0
        # Spec 086: pose inference (~57ms/frame, measured) used to run
        # inside this loop on every frame - it was the single biggest
        # cost limiting real captured fps, which mattered enough to
        # actually lose fast stick-bend motion between frames. Capture
        # now only reads + writes the raw frame; annotate_frames_dir()
        # runs pose detection afterward, once, over the saved files -
        # see its docstring for the measured effect.
        loop_start = time.monotonic()
        while time.monotonic() - loop_start < duration_s:
            ok, frame = cap.read()
            if not ok:
                failed_reads += 1
                if failed_reads in (1, 10, 50) or failed_reads % 100 == 0:
                    logger.warning("record_clip: camera read failed (count=%d)", failed_reads)
                continue
            cv2.imwrite(str(raw_frames_dir / f"frame_{frame_count:06d}.{FRAME_FILE_EXTENSION}"), frame)
            frame_count += 1
        elapsed_s = time.monotonic() - loop_start

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
        on_status("Tunnistetaan käsien asentoja…")
        annotate_frames_dir(raw_frames_dir, annotated_frames_dir, FRAME_FILE_EXTENSION, actual_fps)
        on_status("Lisätään spektrogrammi…")
        # Spec 085: only the annotated clip grows a spectrogram+playhead
        # panel underneath (640x480 -> 640x960) - the raw clip stays at
        # the camera's native resolution. Done here, after capture, since
        # the full audio buffer (needed for the whole-clip spectrogram)
        # only exists once sd.rec() has finished.
        add_spectrograms_to_frames(annotated_frames_dir, audio_buffer, frame_count, FRAME_FILE_EXTENSION, width=FRAME_WIDTH)
        on_status("Yhdistetään ääni ja kuva…")
        encode_frames_with_audio(raw_frames_dir, audio_tmp, raw_out, actual_fps)
        encode_frames_with_audio(annotated_frames_dir, audio_tmp, annotated_out, actual_fps)

    logger.info("record_clip: done -> %s / %s", raw_out.name, annotated_out.name)
    return RecordingResult(raw_out, annotated_out, video_name, audio_name, frame_count, actual_fps)
