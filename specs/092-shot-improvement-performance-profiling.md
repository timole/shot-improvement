# 092 — Performance: measure where the time goes, fuse annotate+spectrogram

## What

An opt-in profiler (`core/profiling.py`, `SHOT_IMPROVEMENT_PROFILE=1`)
that times every stage of the record pipeline and reports memory/disk
alongside it, two headless benchmark tools (`tools/bench_record.py`,
`tools/bench_live.py`), and one proven pixel-identical optimization:
`core/compose.py` fuses pose-annotation and spectrogram-compositing
into a single pass, eliminating a full redundant read+write cycle of
every annotated frame.

## Why

The user asked directly: record a 3-second clip and account for the
time, memory and disk spent in each step, then improve it. Nothing in
the codebase measured this before - `time.monotonic()` appeared in
only four places, all "warn if slower than 1.0s" guards
(`core/session.py:58`, `gui.py:57`); the only way to recover durations
was diffing wall-clock timestamps between log lines.

## Findings

### 1. The post-capture pipeline was disk-I/O bound, not inference-bound

A clean 10s/540-frame recording from `logs/shot-improvement.log`
(14:07:21) showed **231s** for annotate+spectrogram combined -
**427ms/frame**. Pose inference measures at ~55ms/frame (confirmed
live this session, matching spec 086's earlier ~57ms figure). The
other ~370ms/frame was not accounted for anywhere in the code.

Instrumenting the pipeline (this spec) and running a clean, unloaded
3s/82-frame recording gave the actual per-operation breakdown:

| Operation | old: ms/frame |
|---|---|
| `annotate:imread` (read raw BMP) | 51.4 |
| `annotate:detect` (pose inference) | 55.1 |
| `annotate:draw` (`frame.copy()` + box draw) | 2.6 |
| `annotate:imwrite` (write annotated BMP) | 5.1 |
| `spectrogram:imread` (**re-read the same file just written**) | 47.8 |
| `spectrogram:playhead` (`spectrogram.copy()`) | 2.1 |
| `spectrogram:vstack` (new array alloc) | 4.7 |
| `spectrogram:imwrite` (rewrite, now 4.61MB) | 8.1 |
| **Total** | **176.8 ms/frame** |

`spectrogram:imread` - reading back a file this same process had just
written a moment earlier, purely to draw a spectrogram panel
underneath it - was 74% of the spectrogram stage and the single
largest line item after inference itself. Per frame, the old pipeline
moved ~2.76MB read + 2.76MB write + 2.76MB re-read + 4.61MB rewrite =
**~13MB of BMP traffic**, on a machine with **0.54GB free RAM** (so the
Windows file cache absorbs almost none of it) and a **4-core 1.1GHz
Celeron N4120**.

### 2. The C922's `cap.read()` intermittently stalls ~1000ms/call

Confirmed directly with `tools/bench_live.py` (40 consecutive
live-preview reads, no recording): `cap.read()` alone averaged
**1000.3ms/call**; `detect()` in the same run averaged 51.6ms,
unchanged from its normal cost. This is not `PoseDetector` or anything
in this app's code - it is the DirectShow camera backend itself
blocking. The same stall then hit the **recording** capture loop
directly during this session's `tools/bench_record.py` runs, capturing
only 3 frames in 3 seconds (0.99fps) instead of the ~55-80fps this
camera/pipeline normally reaches (specs 088/089).

This matches a dropout spec 090 already flagged as a real,
currently-present hardware/driver issue on this machine (the C922
disappearing from Windows' own device enumeration mid-session) - it
is not something fixable from this codebase, and it is not new to this
session. It does mean two of this session's live `bench_record.py`
runs captured too few frames for a reliable end-to-end wall-clock
comparison; the before/after table above instead compares
**per-operation cost** (ms per `imread` call, ms per `imwrite` call),
which is unaffected by how many frames a stalled capture produced.
**Recommendation**: reseat the C922's USB connection (a different port
if available) and check for a Windows/Logitech driver update; this is
outside what application code can reliably fix.

### 3. Peak temp-disk usage is dominated by the two directories coexisting, not by the redundant I/O

Old peak temp for the 82-frame run: 604.8MB (226.4MB `raw/` + 378MB
`annotated/`, both fully populated at once before ffmpeg consumes
them). Fusing annotate+spectrogram (this spec) does **not** reduce
that peak - both directories still exist simultaneously until encode
runs. What it removes is the *time* spent doing the extra read/write,
not the space. Actually shrinking peak disk usage requires piping the
composited frames straight into ffmpeg so `annotated/` never exists on
disk at all - designed but **not implemented in this round** (see "Out
of scope").

## Implemented

- **`core/profiling.py`** (new): `Profiler.stage(name)` (wall time +
  process working set via `ctypes`→`GetProcessMemoryInfo` + process
  I/O via `GetProcessIoCounters` + temp-dir size via `os.scandir` +
  system-available RAM via `GlobalMemoryStatusEx`) and
  `Profiler.accum(name)` (cheap per-frame perf_counter sum, no
  memory/disk snapshot). Off unless `SHOT_IMPROVEMENT_PROFILE=1`
  (every call is then a bare context-manager enter/exit, no
  `perf_counter` call at all). Deliberately no `psutil` dependency -
  not in `requirements.txt`, and this machine has ~0.5GB RAM to spare.
  ffmpeg is a child process, so its I/O is invisible to
  `GetProcessIoCounters` - encode stages report it honestly instead, as
  input-frames-dir-size (read) and output-file-size (write), via
  `stage()`'s `extra_read_bytes`/`extra_write_bytes` (plain int,
  resolved at entry, or a zero-arg callable, resolved at exit - an
  output file's size isn't known until ffmpeg finishes writing it).
  `report()` prints a stage table + per-op accum table to stdout and
  writes `logs/bench-<timestamp>.json`.
- Instrumentation wired into the shared pipeline functions (one set of
  marks serves both the CLI order and the GUI's different order,
  since both call the same functions): `core/recorder.py` (capture
  loop, `encode_frames_with_audio`, `record_clip`), `core/session.py`
  (capture-side of `read_frame`, `worker()`), `core/compose.py`.
- **`tools/bench_record.py`** (new): a pure-disk micro-benchmark
  (`cv2.imwrite`/`imread` of real-sized BMPs into the real temp
  volume, no camera needed) followed by a real `record_clip(3.0)` with
  profiling forced on.
- **`tools/bench_live.py`** (new): N live-preview `read_frame()` calls
  with no recording, splitting `cap.read()`/`detect()`/`draw()` - the
  direct test for finding #2 above.
- **`core/compose.py`** (new): `compose_annotated_frames()` replaces
  the two-pass `annotate_frames_dir()` → `add_spectrograms_to_frames()`
  pipeline with one pass: read each raw BMP once, detect, draw palm
  boxes **in place** (no `frame.copy()`), composite into one
  preallocated `(height+SPECTROGRAM_HEIGHT, width, 3)` buffer (no
  per-frame `spectrogram.copy()`, no per-frame `np.vstack` allocation),
  write once. `composite_into()` is factored out as a pure function
  so it's directly unit-testable without a camera or a real
  `PoseDetector`. `core/recorder.py::record_clip` and
  `core/session.py::LiveSession`'s background worker both switched to
  it. `annotate_frames_dir` and `add_spectrograms_to_frames` are
  **kept, unchanged, in `core/pose.py`/`core/spectrogram.py`** - no
  longer called from the pipeline, but deliberately preserved as the
  pixel-identity reference the new tests check the fused path against.

Measured per-operation improvement (same hardware, same-sized frames):
`imread` cost drops from 99.1ms/frame (two reads: 51.4 raw + 47.8
re-read) to 20.2ms/frame (one read) - eliminating the redundant
re-read is the single largest win. `draw`+`playhead`+`vstack`'s
9.4ms/frame of allocation/copy becomes one 1.9ms/frame in-place
composite. Extrapolating the full per-frame stage cost to a
same-length clip: **~192ms/frame → ~86ms/frame, a ~55% reduction** in
the compose stage. A full clean end-to-end run to confirm the wall-
clock total directly is blocked on finding #2 (the camera stall) and
should be re-run once that clears.

## Out of scope (designed, not implemented this round)

Per explicit scope agreed before implementation: round one is
pixel-identical only (fuse passes, drop redundant copies) - encoder
preset/CRF changes, Intel Quick Sync, every-Nth-frame pose, and
downscaled model input are all deliberately deferred to a
quality-tradeoff discussion with real numbers, not decided here.

Within the pixel-identical scope, **piping the composited frames
straight into ffmpeg's stdin** (eliminating `annotated/` from disk
entirely, rather than just the redundant re-read/rewrite) was fully
designed but not implemented this round - it is real, additional
implementation risk (three-pipe subprocess threading; this codebase
already hit one real ffmpeg pipe deadlock, spec 090) on top of a
change already worth landing and verifying on its own. The design:
`-f rawvideo -pix_fmt bgr24 -i pipe:0` as ffmpeg's video input, a
dedicated feeder (calling) thread writing composited frames to stdin,
with the existing stderr-drain thread joined by a new stdout-drain
thread (three pipes, three single-purpose actors, no actor ever waits
on a pipe it doesn't own - the deadlock-proof shape). Confirmed safe
to attempt: `actual_fps` is known before this pass starts in both
paths (`core/recorder.py:361`/`core/session.py`'s equivalent, both
before the compose call), and the raw encode correctly stays
file-based either way (it must run first, alone, for the GUI's
`on_raw_ready` latency - spec 091).

## Test plan

- `tests/test_compose.py` (new, 4 tests): `composite_into()` compared
  via `np.array_equal` against the reference
  (`draw_palm_boxes`+`frame.copy()` then
  `np.vstack([frame, with_playhead(...)])`) across 7 `x_fraction`
  values including clamped out-of-range ones, and with no detected
  boxes; a `C_CONTIGUOUS` assertion on the preallocated buffer's
  top/bottom views (the load-bearing premise of the in-place draw);
  a BMP round-trip losslessness check (the load-bearing premise of
  dropping the intermediate file entirely). All pass.
- Full suite: `venv\Scripts\pytest tests\` - **39 passed** (35
  pre-existing + 4 new), no regressions.
- Live: `tools/bench_record.py 3` and `tools/bench_live.py 40` run
  against the real camera/mic this session; both produced the
  per-operation numbers above. The annotated output was probed with
  ffmpeg and confirmed a valid 1280x1200 h264+aac mp4 (correct
  composite dimensions).
- Not verified this session: a full clean end-to-end wall-clock
  comparison at normal fps (blocked on finding #2, the camera stall) -
  the per-operation comparison stands in for it and is not expected to
  change once a clean run is available, since it's driven by removing
  ops (one imread instead of two), not by frame count.
