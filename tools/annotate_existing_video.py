"""One-off tool: produces the `-annotated.mp4` companion (and, spec
099, per-shot snapshot JPEGs) for an existing raw
`shot-improvement-<timestamp>.mp4` that doesn't have them yet (e.g. a
clip captured outside the normal GUI/CLI record flow) - runs it
through the exact same compose_annotated_frames()/
encode_frames_with_audio()/save_shot_images() pipeline a normal
recording uses, so it gets pose boxes, the spectrogram, claps/
puck-speed annotations, and per-shot images identically to any other
clip.

Since the source is already a finished mp4 (not raw per-frame BMPs +
a separate audio.wav, which is what the normal pipeline has at this
point), each frame's real capture timestamp is recovered via ffmpeg's
own reported PTS (`-vf showinfo`) - the same technique used to verify
spec 097's AV-sync fix - rather than assumed evenly spaced.

Usage:
    venv\\Scripts\\python tools\\annotate_existing_video.py <raw_mp4_path>
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import imageio_ffmpeg
import soundfile as sf

from core.compose import compose_annotated_frames, save_shot_images
from core.recorder import FRAME_FILE_EXTENSION, FRAME_HEIGHT, FRAME_WIDTH, encode_frames_with_audio
from core.log_setup import get_logger, setup_logging

setup_logging()
logger = get_logger("annotate_existing_video")


def annotate_existing_video(raw_path: Path) -> tuple[Path, list[Path]]:
    """Returns (annotated_mp4_path, [shot_image_paths])."""
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)
    annotated_path = raw_path.with_name(f"{raw_path.stem}-annotated.mp4")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        raw_frames_dir = tmp_path / "raw"
        annotated_frames_dir = tmp_path / "annotated"
        raw_frames_dir.mkdir()
        annotated_frames_dir.mkdir()
        audio_path = tmp_path / "audio.wav"

        print("Extracting frames + real per-frame timestamps...")
        # -start_number 0: this app's own frame-writing convention is
        # 0-indexed (frame_000000.bmp onward, see core.recorder/session)
        # - encode_frames_with_audio's concat-list builder assumes
        # exactly that. ffmpeg's own frame_%06d output numbering
        # defaults to starting at 1, which would silently mismatch it.
        cmd = [
            ffmpeg_exe, "-y", "-i", str(raw_path), "-vf", "showinfo",
            "-start_number", "0",
            str(raw_frames_dir / f"frame_%06d.{FRAME_FILE_EXTENSION}"),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"frame extraction failed: {result.stderr[-2000:]}")
        frame_times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", result.stderr)]
        frame_count = len(frame_times)
        print(f"  {frame_count} frames, span {frame_times[-1]:.2f}s")

        print("Extracting audio...")
        cmd2 = [ffmpeg_exe, "-y", "-i", str(raw_path), "-vn", "-acodec", "pcm_s16le", str(audio_path)]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        if result2.returncode != 0:
            raise RuntimeError(f"audio extraction failed: {result2.stderr[-2000:]}")
        audio, sample_rate = sf.read(str(audio_path), dtype="int16")

        actual_fps = frame_count / frame_times[-1] if frame_times and frame_times[-1] > 0 else 1.0

        # Sanity check the extracted frame size matches this app's fixed
        # capture resolution - compose_annotated_frames assumes it.
        sample = cv2.imread(str(raw_frames_dir / f"frame_000000.{FRAME_FILE_EXTENSION}"))
        if sample is None or sample.shape[1] != FRAME_WIDTH or sample.shape[0] != FRAME_HEIGHT:
            got = None if sample is None else (sample.shape[1], sample.shape[0])
            raise RuntimeError(f"expected {FRAME_WIDTH}x{FRAME_HEIGHT} frames, got {got}")

        print("Composing pose boxes + spectrogram + claps annotations...")
        compose_annotated_frames(
            raw_frames_dir, annotated_frames_dir, FRAME_FILE_EXTENSION, actual_fps,
            audio, frame_count, FRAME_WIDTH, FRAME_HEIGHT, frame_times=frame_times,
        )

        print("Saving per-shot snapshot images...")
        shot_images = save_shot_images(
            raw_frames_dir, FRAME_FILE_EXTENSION, frame_times, audio, sample_rate,
            raw_path.parent, raw_path.stem,
        )

        print("Encoding annotated.mp4...")
        encode_frames_with_audio(
            annotated_frames_dir, audio_path, annotated_path, actual_fps, frame_count,
            frame_times=frame_times,
        )

    return annotated_path, shot_images


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: annotate_existing_video.py <raw_mp4_path>", file=sys.stderr)
        sys.exit(1)
    raw_path = Path(sys.argv[1])
    out, shot_images = annotate_existing_video(raw_path)
    print(f"Done: {out}")
    for p in shot_images:
        print(f"  shot image: {p}")


if __name__ == "__main__":
    main()
