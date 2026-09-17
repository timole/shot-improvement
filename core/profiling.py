"""Opt-in stage/memory/disk profiler for the record pipeline (spec 092).

Off unless SHOT_IMPROVEMENT_PROFILE=1 (same convention as
core.log_setup's SHOT_IMPROVEMENT_DEBUG - the only other env knob in
this codebase). When off, stage()/accum() are context managers that do
nothing but enter/exit - no perf_counter call, no ctypes call - so
wrapping the pipeline in these is free in normal use. When on, the
per-call cost is two perf_counter() reads (~100ns) plus, for stage()
only, a handful of ctypes calls - negligible against the 57ms+/frame
this app already spends, and stage() is only used around whole
passes/loops, never per-frame (accum() is the per-frame-safe one, and
it skips the memory/disk/temp-dir snapshots entirely).

Three things are measured, deliberately without adding a dependency
(psutil is not in requirements.txt and this machine has ~0.5GB RAM to
spare):

- Wall time: time.perf_counter().
- Memory: ctypes -> psapi.GetProcessMemoryInfo (this process's
  WorkingSetSize/PeakWorkingSetSize) and kernel32.GlobalMemoryStatusEx
  (system-wide available RAM, so file-cache pressure from writing
  hundreds of MB of BMPs is visible even though it isn't "this
  process's" memory).
- Disk: kernel32.GetProcessIoCounters (this process's own
  ReadTransferCount/WriteTransferCount) PLUS a temp-dir high-water-mark
  sampler. GetProcessIoCounters deliberately does NOT capture ffmpeg's
  I/O - ffmpeg runs as a child process, and Windows attributes a
  child's I/O to the child, not the parent. Stages that shell out to
  ffmpeg report disk bytes by measuring the input directory size and
  the output file size directly instead of pretending
  GetProcessIoCounters saw it - see encode_frames_with_audio's use of
  stage(..., extra_read_bytes=..., extra_write_bytes=...).

The temp-dir high-water mark is sampled synchronously at stage
entry/exit (os.scandir over the recording's own tmp dir), not via a
background polling thread. A polling thread was considered and
rejected: this is a 4-core 1.1GHz machine already contended between
the capture/GUI thread and (during a recording) an encode worker
thread (see core.recorder.lower_current_thread_priority) - adding a
third thread that wakes up every N ms purely to stat a directory would
itself perturb the very timings being measured, for a "peak" that
synchronous stage-boundary sampling already reports accurately in
practice: growth here is monotonic within `raw/` and within
`annotated/` (frames are only ever added, never deleted, until ffmpeg
consumes them) and stage boundaries already bracket every phase where
size changes, so the true peak is exactly the size measured at the
last stage boundary of each growth phase - not an approximation.

report() prints a readable table to stdout and writes the same data as
JSON to logs/bench-<timestamp>.json, so before/after runs of
tools/bench_record.py can be diffed mechanically rather than by eye.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional, Union

from .log_setup import get_logger

logger = get_logger("profiling")

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

ENABLED = bool(os.environ.get("SHOT_IMPROVEMENT_PROFILE"))


# --- Windows memory/IO counters via ctypes (no psutil dependency) ---------

class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _win_available() -> bool:
    return os.name == "nt"


def process_memory() -> tuple[int, int]:
    """(working_set_bytes, peak_working_set_bytes) for this process, or
    (0, 0) off Windows / on any ctypes failure - never raises, since
    profiling must never be able to break a real recording."""
    if not _win_available():
        return 0, 0
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)  # type: ignore[attr-defined]
        if not ok:
            return 0, 0
        return counters.WorkingSetSize, counters.PeakWorkingSetSize
    except Exception:
        logger.debug("process_memory: failed", exc_info=True)
        return 0, 0


def system_available_ram() -> int:
    """Bytes of physical RAM currently available system-wide (not just
    to this process) - this machine's whole recording pipeline is
    disk/file-cache bound, so this number moving is as informative as
    this process's own working set."""
    if not _win_available():
        return 0
    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        return status.ullAvailPhys if ok else 0
    except Exception:
        logger.debug("system_available_ram: failed", exc_info=True)
        return 0


def process_io_bytes() -> tuple[int, int]:
    """(read_bytes, write_bytes) this process itself has done via
    ReadFile/WriteFile since it started. Does NOT include child
    processes (ffmpeg) - see module docstring."""
    if not _win_available():
        return 0, 0
    try:
        counters = _IO_COUNTERS()
        handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        ok = ctypes.windll.kernel32.GetProcessIoCounters(handle, ctypes.byref(counters))  # type: ignore[attr-defined]
        if not ok:
            return 0, 0
        return counters.ReadTransferCount, counters.WriteTransferCount
    except Exception:
        logger.debug("process_io_bytes: failed", exc_info=True)
        return 0, 0


def dir_size(path: Path) -> tuple[int, int]:
    """(total_bytes, file_count) of a directory tree. Cheap relative to
    the multi-MB-per-file BMPs this app writes; used at stage
    boundaries, not per-frame."""
    total = 0
    count = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                total += entry.stat().st_size
                count += 1
            elif entry.is_dir():
                sub_total, sub_count = dir_size(Path(entry.path))
                total += sub_total
                count += sub_count
    except (FileNotFoundError, NotADirectoryError):
        pass
    return total, count


# --- The profiler itself ---------------------------------------------------

@dataclass
class _StageRecord:
    name: str
    elapsed_s: float
    working_set_delta: int
    peak_working_set: int
    process_read_delta: int
    process_write_delta: int
    extra_read_bytes: int
    extra_write_bytes: int
    temp_dir_bytes_before: int
    temp_dir_bytes_after: int
    temp_dir_files_after: int
    system_available_ram_after: int


@dataclass
class _AccumRecord:
    name: str
    calls: int = 0
    total_s: float = 0.0


@dataclass
class Profiler:
    """One Profiler per recording. Pass temp_dir once the recording's
    tempfile.TemporaryDirectory is known so stage() can report temp
    high-water-mark bytes/files alongside timing."""

    enabled: bool = ENABLED
    temp_dir: Optional[Path] = None
    stages: list[_StageRecord] = field(default_factory=list)
    accums: dict[str, _AccumRecord] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)
    _run_start: float = field(default_factory=time.perf_counter)

    def set_temp_dir(self, path: Path) -> None:
        self.temp_dir = path

    def note(self, key: str, value: object) -> None:
        """Records one piece of run metadata (camera fourcc, frame
        count, achieved fps, ...) into the report alongside the stage
        table."""
        self.meta[key] = value

    @contextmanager
    def stage(
        self,
        name: str,
        extra_read_bytes: Union[int, Callable[[], int]] = 0,
        extra_write_bytes: Union[int, Callable[[], int]] = 0,
    ) -> Iterator[None]:
        """Times one whole pass (encode, annotate, spectrogram, ...).
        extra_read_bytes/extra_write_bytes let a stage that shells out
        to ffmpeg (whose I/O this process's own counters can't see)
        report the input-dir/output-file sizes it knows about instead
        of silently under-counting - see module docstring. Either may
        be a plain int (resolved once, at entry - e.g. an input dir
        size that doesn't change during the stage) or a zero-arg
        callable (resolved at exit - e.g. an output file whose size
        isn't known until the stage finishes; wrapped in try/except so
        a failed stage whose output never got written doesn't itself
        raise while unwinding)."""
        if not self.enabled:
            yield
            return
        ws_before, _ = process_memory()
        read_before, write_before = process_io_bytes()
        temp_before, _ = dir_size(self.temp_dir) if self.temp_dir else (0, 0)
        resolved_read = extra_read_bytes() if callable(extra_read_bytes) else extra_read_bytes
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - t0
            ws_after, peak_ws = process_memory()
            read_after, write_after = process_io_bytes()
            temp_after, temp_files = dir_size(self.temp_dir) if self.temp_dir else (0, 0)
            if callable(extra_write_bytes):
                try:
                    resolved_write = extra_write_bytes()
                except Exception:
                    resolved_write = 0
            else:
                resolved_write = extra_write_bytes
            self.stages.append(
                _StageRecord(
                    name=name,
                    elapsed_s=elapsed,
                    working_set_delta=ws_after - ws_before,
                    peak_working_set=peak_ws,
                    process_read_delta=read_after - read_before,
                    process_write_delta=write_after - write_before,
                    extra_read_bytes=resolved_read,
                    extra_write_bytes=resolved_write,
                    temp_dir_bytes_before=temp_before,
                    temp_dir_bytes_after=temp_after,
                    temp_dir_files_after=temp_files,
                    system_available_ram_after=system_available_ram(),
                )
            )

    @contextmanager
    def accum(self, name: str) -> Iterator[None]:
        """Cheap per-iteration timer for hot per-frame loops - no
        memory/disk/temp-dir snapshot, just perf_counter deltas summed
        under `name` so a stage's inner loop (e.g. annotate's imread vs
        detect vs imwrite) can be broken down without paying stage()'s
        heavier per-call cost 500+ times per clip."""
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            rec = self.accums.setdefault(name, _AccumRecord(name))
            rec.calls += 1
            rec.total_s += time.perf_counter() - t0

    def report(self, label: str = "record") -> dict:
        """Prints a readable table to stdout and writes the same data
        as logs/bench-<timestamp>.json. Returns the dict that was
        written, for callers (tests, bench scripts) that want it
        in-process too. A no-op returning {} if profiling is off."""
        if not self.enabled:
            return {}

        total_elapsed = time.perf_counter() - self._run_start
        lines = []
        lines.append(f"\n=== {label} profile ===")
        if self.meta:
            lines.append("-- run metadata --")
            for k, v in self.meta.items():
                lines.append(f"  {k}: {v}")

        lines.append(f"{'stage':<32}{'time_s':>9}{'ws_delta_mb':>13}{'peak_ws_mb':>12}"
                     f"{'read_mb':>10}{'write_mb':>10}{'temp_mb':>10}{'temp_files':>11}{'sys_avail_mb':>13}")
        for s in self.stages:
            read_mb = (s.process_read_delta + s.extra_read_bytes) / 1e6
            write_mb = (s.process_write_delta + s.extra_write_bytes) / 1e6
            lines.append(
                f"{s.name:<32}{s.elapsed_s:>9.2f}{s.working_set_delta / 1e6:>13.1f}"
                f"{s.peak_working_set / 1e6:>12.1f}{read_mb:>10.1f}{write_mb:>10.1f}"
                f"{s.temp_dir_bytes_after / 1e6:>10.1f}{s.temp_dir_files_after:>11d}"
                f"{s.system_available_ram_after / 1e6:>13.1f}"
            )
        lines.append(f"{'TOTAL (wall clock)':<32}{total_elapsed:>9.2f}")

        if self.accums:
            lines.append("-- per-frame breakdown (accum) --")
            lines.append(f"{'name':<32}{'calls':>8}{'total_s':>10}{'avg_ms':>10}")
            for name, rec in sorted(self.accums.items()):
                avg_ms = (rec.total_s / rec.calls * 1000) if rec.calls else 0.0
                lines.append(f"{name:<32}{rec.calls:>8}{rec.total_s:>10.2f}{avg_ms:>10.2f}")

        peak_temp_bytes = max((s.temp_dir_bytes_after for s in self.stages), default=0)
        peak_temp_files = max((s.temp_dir_files_after for s in self.stages), default=0)
        lines.append(f"peak temp dir: {peak_temp_bytes / 1e6:.1f} MB, {peak_temp_files} files")

        text = "\n".join(lines)
        print(text)
        logger.info("profile report (%s):\n%s", label, text)

        report_dict = {
            "label": label,
            "total_elapsed_s": total_elapsed,
            "meta": self.meta,
            "stages": [vars(s) for s in self.stages],
            "accums": {k: vars(v) for k, v in self.accums.items()},
            "peak_temp_dir_bytes": peak_temp_bytes,
            "peak_temp_dir_files": peak_temp_files,
        }

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        out_path = LOG_DIR / f"bench-{time.strftime('%Y%m%d%H%M%S')}.json"
        out_path.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
        print(f"(written to {out_path})")

        return report_dict


# A no-op profiler shared by callers that don't want to construct their
# own Profiler() (e.g. code paths not currently under benchmark) - same
# free-when-off behavior via .enabled=False.
NULL_PROFILER = Profiler(enabled=False)
