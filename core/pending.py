"""Durable storage for raw captures whose processing (pose annotation,
spectrogram compositing, ffmpeg encoding) has been deliberately
deferred (spec 093) - the GUI's "Vain nauhoitus (käsittele myöhemmin)"
(recording only, process later) mode.

Why: this app's post-capture processing is CPU-heavy enough on modest
hardware to noticeably slow the machine down while it runs - spec 091
already prioritizes a NEW recording's own capture over an older
recording's background processing under contention, but processing
still competes for the same weak CPU, and the user asked for a way to
avoid that contention altogether rather than just deprioritize it.
Recording-only mode sidesteps it entirely: capture does only a camera
read + one disk write per frame (as it already did - see
core.recorder's module docstring), and nothing else runs until the
user explicitly asks for it, letting several recordings be captured
back to back at full speed with all the slow work done afterward, once.

Unlike a normal recording (core.session.LiveSession.start_recording),
which streams frames into a tempfile.TemporaryDirectory() deleted the
moment processing finishes, a deferred recording's frames are written
straight into PENDING_DIR/<timestamp>/ so they survive past the
recording call itself - across GUI restarts, even - until
process_pending_recording() turns them into the usual raw + annotated
mp4s.

Spec 110: a processed entry's raw frames/audio are kept, not deleted -
moved to PROCESSED_DIR (PENDING_DIR/processed/<timestamp>/) so
list_pending() stops seeing it as still-pending, without losing the
files. Explicit, durable user request - "keep all files, do not delete
them" - not a temporary flag like spec 105's KEEP_TEMP_DIR_FOR_
INSPECTION.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import soundfile as sf

from .claps import PUCK_TRAVEL_DISTANCE_M
from .log_setup import get_logger
from .profiling import NULL_PROFILER, Profiler
from .rink import DEFAULT_TARGET, TARGETS

logger = get_logger("pending")

PENDING_DIR = Path(__file__).resolve().parent.parent / "pending"
META_FILENAME = "meta.json"
# Spec 110: where a processed entry's raw frames/audio move to, instead
# of being deleted - see archive_processed().
PROCESSED_DIR = PENDING_DIR / "processed"


@dataclass(frozen=True)
class PendingRecording:
    dir_path: Path
    timestamp: str
    frame_count: int
    actual_fps: float
    duration_s: float
    video_name: str
    audio_name: Optional[str]
    created_at: str  # ISO 8601 - see created_label()
    # Spec 097: each frame's real capture timestamp, persisted here since
    # processing can happen long after capture (that's the whole point of
    # this module) - see core.recorder's module docstring. Empty for a
    # pending/ entry saved before spec 097 (an interrupted-then-resumed
    # queue across an app upgrade) - process_pending_recording() falls
    # back to the old even-spacing assumption in that case.
    frame_times: list[float] = field(default_factory=list)
    # Spec 128: the shot position's puck-travel distance chosen when this
    # was recorded (processing can happen long after) - see core.claps.
    distance_m: float = PUCK_TRAVEL_DISTANCE_M
    # Spec 137: what the puck hits ("goal" / "end"), likewise kept for later.
    target: str = DEFAULT_TARGET

    @property
    def raw_dir(self) -> Path:
        return self.dir_path / "raw"

    @property
    def audio_path(self) -> Path:
        return self.dir_path / "audio.wav"

    def created_label(self) -> str:
        try:
            return datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return self.created_at


def write_meta(
    dir_path: Path,
    frame_count: int,
    actual_fps: float,
    duration_s: float,
    video_name: str,
    audio_name: Optional[str],
    frame_times: Optional[list[float]] = None,
    distance_m: float = PUCK_TRAVEL_DISTANCE_M,
    target: str = DEFAULT_TARGET,
) -> None:
    meta = {
        "frame_count": frame_count,
        "actual_fps": actual_fps,
        "duration_s": duration_s,
        "video_name": video_name,
        "audio_name": audio_name,
        "created_at": datetime.now().isoformat(),
        "frame_times": frame_times or [],
        "distance_m": distance_m,
        "target": target,
    }
    (dir_path / META_FILENAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def list_pending(pending_dir: Path = PENDING_DIR) -> list[PendingRecording]:
    """Oldest first - the directory name is the capture timestamp
    (core.recorder.timestamp_for_filename's format), lexicographically
    sortable as-is. The GUI's "Käsittele odottavat" button processes
    this list in order, so a burst of recordings gets processed in the
    order it was captured. Any entry missing or with an unreadable
    meta.json is skipped with a warning (an interrupted capture, or a
    directory that shouldn't be there) rather than raising - one bad
    entry must never block the rest of the queue."""
    if not pending_dir.exists():
        return []
    items: list[PendingRecording] = []
    for entry in sorted(pending_dir.iterdir()):
        if not entry.is_dir() or entry.name == PROCESSED_DIR.name:
            continue
        meta_path = entry / META_FILENAME
        if not meta_path.exists():
            logger.warning("list_pending: %s has no %s, skipping (interrupted capture?)", entry.name, META_FILENAME)
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            items.append(PendingRecording(
                dir_path=entry,
                timestamp=entry.name,
                frame_count=int(meta["frame_count"]),
                actual_fps=float(meta["actual_fps"]),
                duration_s=float(meta["duration_s"]),
                video_name=str(meta["video_name"]),
                audio_name=meta.get("audio_name"),
                created_at=str(meta["created_at"]),
                frame_times=[float(t) for t in meta.get("frame_times", [])],
                distance_m=float(meta.get("distance_m", PUCK_TRAVEL_DISTANCE_M)),
                target=meta.get("target") if meta.get("target") in TARGETS else DEFAULT_TARGET,
            ))
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            logger.warning("list_pending: %s has an unreadable %s, skipping", entry.name, META_FILENAME, exc_info=True)
    return items


def delete_pending(dir_path: Path) -> None:
    shutil.rmtree(dir_path, ignore_errors=True)


def archive_processed(dir_path: Path, processed_dir: Path = PROCESSED_DIR) -> Path:
    """Moves a successfully-processed entry out of pending_dir into
    processed_dir (spec 110) - keeps every file (raw frames, audio.wav,
    meta.json) instead of deleting them, while getting it out of
    list_pending()'s view so it doesn't linger in the "Odottaa
    käsittelyä" queue forever. processed_dir is overridable (like
    list_pending's pending_dir) so tests don't touch the real
    PROCESSED_DIR."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / dir_path.name
    shutil.move(str(dir_path), str(dest))
    return dest


def process_pending_recording(
    item: PendingRecording,
    out_dir: Path,
    on_done: Callable[[Optional[object]], None],
    dispatch: Callable[[Callable[[], None]], None] = lambda fn: fn(),
    on_progress: Callable[[str, float], None] = lambda stage, fraction: None,
    on_raw_ready: Callable[[Path], None] = lambda raw_path: None,
    profiler: Profiler = NULL_PROFILER,
) -> None:
    """Turns one already-captured pending recording into the raw and
    annotated mp4s (spec 093) - the same encode -> compose -> encode
    sequence core.session.LiveSession's immediate-processing worker
    runs right after capture (core/session.py's _finish_immediate_
    recording), run here instead on request, against frames that may
    have been captured minutes or a whole session earlier. `on_done`
    receives a core.session.RecordingResult on success, None on
    failure - deliberately typed loosely (`object`) here to avoid an
    import cycle (core.session already imports this module for
    PENDING_DIR/write_meta); the real type is imported locally, inside
    worker(), once it's actually needed.

    Archives `item.dir_path` (spec 110 - moved to PROCESSED_DIR, not
    deleted) once both mp4s are written; leaves it in place untouched
    if anything fails, so a transient error (camera unrelated at this
    point - no camera access happens here at all) never loses footage,
    and the item stays in the pending queue ready to retry.

    Runs on its own background thread at lowered OS priority (spec
    091's pattern, reused here) - a new recording must never be slowed
    down by this, which is the entire point of deferring processing in
    the first place."""
    from .compose import compose_annotated_frames, save_shot_images
    from .recorder import (
        FRAME_FILE_EXTENSION,
        FRAME_HEIGHT,
        FRAME_WIDTH,
        SAMPLE_RATE,
        encode_frames_with_audio,
        lower_current_thread_priority,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_out = out_dir / f"shot-improvement-{item.timestamp}.mp4"
    annotated_out = out_dir / f"shot-improvement-{item.timestamp}-annotated.mp4"

    def report(stage: str) -> Callable[[int, int], None]:
        return lambda done, total: dispatch(lambda: on_progress(stage, done / total if total else 1.0))

    def worker() -> None:
        from .session import RecordingResult  # local: avoids a session<->pending import cycle

        lower_current_thread_priority()
        result: Optional[RecordingResult] = None
        try:
            encode_frames_with_audio(
                item.raw_dir, item.audio_path, raw_out, item.actual_fps, item.frame_count,
                frame_times=item.frame_times,
                on_progress=report("Tallennetaan levylle (raaka)"),
                profiler=profiler,
            )
            dispatch(lambda: on_raw_ready(raw_out))

            audio_buffer, _sample_rate = sf.read(str(item.audio_path), dtype="int16")
            with tempfile.TemporaryDirectory() as tmp:
                annotated_dir = Path(tmp) / "annotated"
                annotated_dir.mkdir()
                compose_annotated_frames(
                    item.raw_dir, annotated_dir, FRAME_FILE_EXTENSION, item.actual_fps,
                    audio_buffer, item.frame_count, FRAME_WIDTH, FRAME_HEIGHT,
                    frame_times=item.frame_times,
                    on_progress=report("Tunnistetaan käsien asentoja ja spektrogrammi"),
                    profiler=profiler, distance_m=item.distance_m,
                    hands_json=out_dir / f"shot-improvement-{item.timestamp}-hands.json",
                )
                # Spec 099: a plain snapshot + speed label per detected
                # shot, alongside the raw/annotated mp4s.
                with profiler.accum("shot_images"):
                    save_shot_images(
                        item.raw_dir, FRAME_FILE_EXTENSION, item.frame_times, audio_buffer, SAMPLE_RATE,
                        out_dir, f"shot-improvement-{item.timestamp}", distance_m=item.distance_m, target=item.target,
                    )
                encode_frames_with_audio(
                    annotated_dir, item.audio_path, annotated_out, item.actual_fps, item.frame_count,
                    frame_times=item.frame_times,
                    on_progress=report("Tallennetaan levylle"),
                    profiler=profiler,
                )
            result = RecordingResult(raw_out, annotated_out, item.frame_count, item.actual_fps)
            archived_to = archive_processed(item.dir_path)
            logger.info(
                "process_pending_recording: done -> %s / %s (source kept, archived to %s)",
                raw_out.name, annotated_out.name, archived_to,
            )
        except Exception:
            logger.exception("process_pending_recording: failed for %s (kept for retry)", item.dir_path)
            result = None
        finally:
            dispatch(lambda: on_done(result))

    threading.Thread(target=worker, name="shot-improvement-process-pending", daemon=True).start()
