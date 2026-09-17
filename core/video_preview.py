"""Storage-agnostic preview-image generation (spec 094, split out spec
095 so both core.cloud_sync (GCS) and core.azure_sync (Azure Blob) can
share it without duplicating the ffmpeg logic or importing each
other's private functions).

Deliberately the video's own FIRST frame, not a seek into the middle -
this machine's camera has a documented, currently-active intermittent
driver stall (specs 090/092/093) that can produce clips only a handful
of frames long, and seeking past a short clip's end is a failure mode
a fixed first-frame grab simply can't hit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg

from .log_setup import get_logger

logger = get_logger("video_preview")

PREVIEW_SUFFIX = ".jpg"


def preview_name(video_name: str) -> str:
    return Path(video_name).stem + PREVIEW_SUFFIX


def generate_preview(video_path: Path, out_path: Path) -> bool:
    """Extracts video_path's first frame as a JPEG into out_path via
    ffmpeg. Returns False (out_path not written) on any failure - a
    thumbnail is a nice-to-have, never allowed to fail the video's own
    upload."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "3",  # ffmpeg mjpeg qscale: lower is better quality; 3 is a good size/quality tradeoff for a gallery thumbnail
        str(out_path),
    ]
    try:
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.CalledProcessError, OSError):
        logger.warning("generate_preview: failed for %s", video_path.name, exc_info=True)
        return False
    return out_path.exists()
