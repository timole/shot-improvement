"""Live-preview micro-benchmark (spec 092): opens a core.session.
LiveSession (real camera+mic+detector, no GUI) and calls read_frame()
in a tight loop WITHOUT ever starting a recording, splitting
cap.read()/detect()/draw() timing via core.profiling's accum().

Exists to directly test one specific anomaly found in
logs/shot-improvement.log: idle live-preview read_frame() logging a
steady ~1.05s/frame (core.session.SLOW_FRAME_WARN_THRESHOLD_S=1.0
tripped on almost every tick) - is that cap.read() blocking on the
camera, or PoseDetector.detect()? Only a per-call breakdown answers
that; the existing 1.0s "slow" warning in the log doesn't say which
part was slow.

Usage:
    venv\\Scripts\\python tools\\bench_live.py [num_frames]   # default 200
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.log_setup import get_logger, setup_logging
from core.profiling import Profiler
from core.session import LiveSession

setup_logging()
logger = get_logger("bench_live")

DEFAULT_FRAMES = 200


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    num_frames = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FRAMES

    print("Opening LiveSession (camera + mic enumeration + PoseDetector load)...")
    try:
        session = LiveSession()
    except Exception as exc:
        logger.exception("bench_live: LiveSession() failed")
        print(f"Could not open a live session: {exc}", file=sys.stderr)
        sys.exit(1)

    width, height, fps = session.camera_info()
    print(f"Camera: {session.video_name!r} negotiated {width}x{height} @ {fps:.1f} fps (not recording)")

    profiler = Profiler(enabled=True)
    session.set_profiler(profiler)

    print(f"Reading {num_frames} live-preview frames (no recording)...")
    ok_count = 0
    t0 = time.perf_counter()
    try:
        for i in range(num_frames):
            frame_start = time.perf_counter()
            frame = session.read_frame()
            elapsed = time.perf_counter() - frame_start
            if frame is not None:
                ok_count += 1
            if i < 5 or i % 50 == 0:
                print(f"  frame {i}: {elapsed * 1000:.1f} ms {'(read failed)' if frame is None else ''}")
    finally:
        total_s = time.perf_counter() - t0
        session.close()

    print(f"\n{ok_count}/{num_frames} frames read OK in {total_s:.2f}s ({ok_count / total_s:.1f} fps effective)")
    profiler.report("live-preview")


if __name__ == "__main__":
    main()
