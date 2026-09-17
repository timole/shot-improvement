"""One-off retrofit (spec 097) for clips recorded before the audio/video
sync fix: shifts each existing recording's audio track later by a fixed
offset (default 1.0s, matching the user's own measured estimate on
shot-improvement-20260915112625-annotated.mp4 - "audio comes
approximately 1 second early") and writes the result alongside the
original as `<name>-fixed.mp4`. Originals are left untouched.

This is an APPROXIMATION, not the real fix - the real fix
(core.recorder.encode_frames_with_audio's new ffconcat/frame_times
path) only applies to recordings made from now on, since it needs each
frame's real per-capture timestamp, which was never recorded for
existing clips (their raw frames are long gone - only the final muxed
mp4 survives). A single constant-offset shift can't undo the
time-varying distortion spec 097 identified (a camera stall mid-clip
shifts frames near it much more than frames far from it) - it just
corrects the average/most-noticeable drift the user actually reported,
which is the best available fix for footage whose original frame
timing is unrecoverable.

Uses stream copy (-c:v copy -c:a copy), not re-encoding - lossless and
fast; only the audio stream's timestamps move (-itsoffset on a second
read of the same file, mapped for its audio stream only), video is
untouched.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "recordings"
DEFAULT_OFFSET_S = 1.0


def fix_one(src: Path, offset_s: float = DEFAULT_OFFSET_S) -> Path:
    """Writes src's audio-delayed twin as `<src stem>-fixed.mp4` next to
    it and returns that path. Raises subprocess.CalledProcessError if
    ffmpeg fails."""
    out_path = src.with_name(f"{src.stem}-fixed.mp4")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        "-i", str(src),
        "-itsoffset", f"{offset_s:.3f}",
        "-i", str(src),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "copy",
        "-shortest",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)
    return out_path


def main() -> None:
    offset_s = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OFFSET_S
    candidates = sorted(
        p for p in RECORDINGS_DIR.glob("*.mp4")
        if not p.stem.endswith("-fixed")
    )
    print(f"Found {len(candidates)} existing clip(s) to fix (offset={offset_s:.2f}s, delaying audio)")
    fixed = 0
    for src in candidates:
        out_path = src.with_name(f"{src.stem}-fixed.mp4")
        if out_path.exists():
            print(f"  skip (already fixed): {src.name}")
            continue
        print(f"  fixing: {src.name} -> {out_path.name}")
        try:
            fix_one(src, offset_s)
            fixed += 1
        except subprocess.CalledProcessError as e:
            print(f"    FAILED: {e.stderr[-1000:] if e.stderr else e}")
    print(f"Done: {fixed}/{len(candidates)} fixed.")


if __name__ == "__main__":
    main()
