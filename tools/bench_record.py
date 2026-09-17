"""Headless benchmark (spec 092): records one clip via core.recorder.
record_clip with profiling forced on, and prints/saves the stage-by-
stage time/memory/disk report (core.profiling).

Also runs a short pure-disk micro-benchmark FIRST (cv2.imwrite/imread
of BMPs at this app's real frame sizes, into the real temp volume) -
this alone answers whether the pipeline is disk-bound, in seconds,
without needing the camera.

Usage:
    venv\\Scripts\\python tools\\bench_record.py [seconds]   # default 3
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

# tools/ is not the repo root - python inserts THIS script's directory
# at sys.path[0], not the repo root, so `import core` needs a hand here
# (record.py/gui.py don't need this since they already live at the
# repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from core.log_setup import get_logger, setup_logging
from core.profiling import Profiler
from core.recorder import FRAME_HEIGHT, FRAME_WIDTH, record_clip
from core.spectrogram import SPECTROGRAM_HEIGHT

setup_logging()
logger = get_logger("bench_record")

DISK_BENCH_FRAMES = 20


def disk_microbenchmark() -> None:
    """Answers "is this pipeline disk-bound?" directly: writes/reads
    DISK_BENCH_FRAMES synthetic BMPs at this app's two real frame sizes
    (raw 1280x720, annotated composite 1280x(720+SPECTROGRAM_HEIGHT))
    into the real OS temp volume - same cv2.imwrite/imread calls the
    pipeline itself uses, isolated from camera/pose/ffmpeg cost."""
    print(f"\n=== disk micro-benchmark ({DISK_BENCH_FRAMES} frames/size, real temp volume) ===")
    rng = np.random.default_rng(0)
    sizes = {
        "raw (1280x720 BMP)": (FRAME_HEIGHT, FRAME_WIDTH),
        f"annotated composite (1280x{FRAME_HEIGHT + SPECTROGRAM_HEIGHT} BMP)": (FRAME_HEIGHT + SPECTROGRAM_HEIGHT, FRAME_WIDTH),
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for label, (h, w) in sizes.items():
            frame = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
            paths = [tmp_path / f"bench_{label[:4]}_{i}.bmp" for i in range(DISK_BENCH_FRAMES)]

            t0 = time.perf_counter()
            for p in paths:
                cv2.imwrite(str(p), frame)
            write_s = time.perf_counter() - t0

            t0 = time.perf_counter()
            for p in paths:
                cv2.imread(str(p))
            read_s = time.perf_counter() - t0

            frame_mb = frame.nbytes / 1e6
            print(
                f"  {label}: {frame_mb:.2f} MB/frame -> "
                f"write {write_s / DISK_BENCH_FRAMES * 1000:.1f} ms/frame "
                f"({frame_mb / (write_s / DISK_BENCH_FRAMES):.0f} MB/s), "
                f"read {read_s / DISK_BENCH_FRAMES * 1000:.1f} ms/frame "
                f"({frame_mb / (read_s / DISK_BENCH_FRAMES):.0f} MB/s)"
            )
            for p in paths:
                p.unlink(missing_ok=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0

    disk_microbenchmark()

    print(f"\n=== recording {duration:.1f}s via core.recorder.record_clip (camera required) ===")
    profiler = Profiler(enabled=True)

    def on_progress(stage: str, fraction: float) -> None:
        print(f"\r{stage}: {fraction * 100:.0f}%" + (" " * 10 if fraction >= 1.0 else ""), end="\n" if fraction >= 1.0 else "", flush=True)

    try:
        result = record_clip(duration_s=duration, on_status=print, on_progress=on_progress, profiler=profiler)
    except Exception as exc:
        logger.exception("bench_record: record_clip failed")
        print(f"Recording failed: {exc}", file=sys.stderr)
        sys.exit(1)

    profiler.report("cli-record")
    print(f"\nRaw:       {result.raw_path}")
    print(f"Annotated: {result.annotated_path}")
    print(f"Frames: {result.frame_count}  Achieved fps: {result.actual_fps:.1f}")


if __name__ == "__main__":
    main()
