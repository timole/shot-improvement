"""A clip player for gui.py's native in-window playback (spec 084).

Owns one cv2.VideoCapture + pre-decoded audio for a single recording,
and tracks play/pause/position/speed/volume/loop state. Kept
independent of Tkinter on purpose: everything except actually painting
a frame or updating a widget lives here, so the bulk of the logic is
unit-testable without a GUI.

Audio only plays during normal Play, at the current speed (resampled
to match - this shifts pitch, like a tape speed change, rather than
true pitch-preserving time-stretching, which would need a new
dependency; simplest option that still lets you hear roughly when the
impact happens). Scrubbing, frame-stepping, and paused states are
silent - see spec 084's "Out of scope" for why pause/seek didn't get
full audio resync.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import imageio_ffmpeg
import numpy as np
import sounddevice as sd
import soundfile as sf

from .log_setup import get_logger

logger = get_logger("playback")

SPEED_OPTIONS = [0.25, 0.5, 1.0, 1.5, 2.0]
SKIP_SECONDS = 5.0


def format_time(seconds: float) -> str:
    """m:ss.d, e.g. 63.25 -> "1:03.3" - sub-second precision matters
    since these clips are themselves only a few seconds long."""
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:04.1f}"


def resample_for_speed(audio: np.ndarray, speed: float) -> np.ndarray:
    """Linear-interpolation resampling to play audio at `speed` -
    changes pitch, not true time-stretching. See module docstring."""
    if speed == 1.0 or len(audio) == 0:
        return audio
    n = len(audio)
    new_n = max(1, int(round(n / speed)))
    old_idx = np.arange(n)
    new_idx = np.linspace(0, n - 1, new_n)
    if audio.ndim == 1:
        return np.interp(new_idx, old_idx, audio).astype(audio.dtype)
    channels = [np.interp(new_idx, old_idx, audio[:, c]) for c in range(audio.shape[1])]
    return np.stack(channels, axis=1).astype(audio.dtype)


def extract_audio(path: Path) -> tuple[Optional[np.ndarray], Optional[int]]:
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "audio.wav"
            subprocess.run(
                [ffmpeg_exe, "-y", "-i", str(path), "-vn", str(wav_path)],
                check=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return sf.read(wav_path)
    except Exception:
        logger.exception("extract_audio: failed for %s", path.name)
        return None, None


class ClipPlayer:
    def __init__(self, path: Path, output_device: Optional[int] = None):
        self.path = path
        self.output_device = output_device

        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise RuntimeError(f"Videon avaaminen epäonnistui: {path.name}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 15.0
        self.total_frames = max(1, int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.duration_s = self.total_frames / self.fps if self.fps > 0 else 0.0
        self.audio_data, self.audio_samplerate = extract_audio(path)

        self.frame_idx = 0
        self.playing = False
        self.speed = 1.0
        self.loop = False
        self.volume = 1.0
        self.muted = False

        logger.info(
            "ClipPlayer opened %s: %d frames, %.1ffps, %.2fs, audio=%s",
            path.name, self.total_frames, self.fps, self.duration_s, self.audio_data is not None,
        )

    @property
    def position_s(self) -> float:
        return self.frame_idx / self.fps if self.fps > 0 else 0.0

    @property
    def at_end(self) -> bool:
        return self.frame_idx >= self.total_frames - 1

    def frame_interval_s(self) -> float:
        return (1.0 / self.fps) / self.speed if self.fps > 0 and self.speed > 0 else 1.0

    def read_current_frame(self):
        """Reads and returns the frame at frame_idx without changing it -
        used by seek/step. Leaves the capture positioned so a
        subsequent advance() naturally continues from frame_idx + 1."""
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.frame_idx)
        ok, frame = self.cap.read()
        return frame if ok else None

    def step(self, delta_frames: int):
        self.pause()
        self.frame_idx = max(0, min(self.total_frames - 1, self.frame_idx + delta_frames))
        return self.read_current_frame()

    def seek_to_seconds(self, seconds: float):
        self.frame_idx = max(0, min(self.total_frames - 1, int(seconds * self.fps)))
        return self.read_current_frame()

    def advance(self):
        """Reads the next frame during normal playback, advancing
        frame_idx. Returns None once the clip has run out of frames."""
        ok, frame = self.cap.read()
        if not ok:
            return None
        self.frame_idx += 1
        return frame

    def play(self) -> None:
        if self.playing:
            return
        if self.at_end:
            self.frame_idx = 0
            self.read_current_frame()
        self.playing = True
        self._play_audio_from_current_position()
        logger.info("ClipPlayer: play (position=%.2fs speed=%.2fx)", self.position_s, self.speed)

    def pause(self) -> None:
        if not self.playing:
            return
        self.playing = False
        sd.stop()
        logger.info("ClipPlayer: pause (position=%.2fs)", self.position_s)

    def stop(self) -> None:
        self.pause()
        self.frame_idx = 0
        self.read_current_frame()
        logger.info("ClipPlayer: stop (reset to 0)")

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        logger.info("ClipPlayer: speed -> %.2fx", speed)
        if self.playing:
            self._play_audio_from_current_position()

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        if self.playing:
            self._play_audio_from_current_position()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if self.playing:
            self._play_audio_from_current_position()

    def _play_audio_from_current_position(self) -> None:
        sd.stop()
        if self.audio_data is None or self.muted or self.volume <= 0:
            return
        start_sample = int(self.position_s * self.audio_samplerate)
        remaining = self.audio_data[start_sample:]
        if len(remaining) == 0:
            return
        adjusted = (resample_for_speed(remaining, self.speed).astype(np.float64) * self.volume)
        try:
            sd.play(adjusted.astype(self.audio_data.dtype), self.audio_samplerate, device=self.output_device)
        except Exception:
            logger.exception("ClipPlayer: audio playback failed")

    def close(self) -> None:
        sd.stop()
        self.cap.release()
        logger.info("ClipPlayer closed %s", self.path.name)
