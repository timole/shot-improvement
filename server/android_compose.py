"""Server-side composited shot video (spec 149) - combines a phone
shot's own portrait video, audio and a spectrogram (the whole clip,
rendered once, with a moving line marking "now" - the same idea as
core/compose.py's playhead for the desktop app, built fresh here
rather than shared code) into ONE playable .mp4, so the web gallery's
single-shot view can be one <video> tag instead of juggling separate
video/audio/spectrogram elements in sync by hand.

Video on top, spectrogram band on the bottom - matches the order
MainActivity's own ShotDetailDialog shows them in (spec 149 reordered
it from spectrogram-then-video to video-then-spectrogram specifically
to match this).

This module itself only ever shells out to ffmpeg (rotation,
spectrogram rendering, compositing, encoding) - no cv2/mediapipe here,
so the deployed server image stays exactly as small/simple as before
this spec. The ffmpeg binary comes from the `imageio-ffmpeg` PyPI
package, which bundles a static build for the current platform - no
apt-get, no Dockerfile change, just another line in requirements.txt.

Hand-position boxes (the yellow squares core/compose.py's own
annotated videos already draw) are NOT rendered here - MediaPipe pose
detection is far too slow (tens of ms per frame) to run inline in an
HTTP request. Instead, tools/compose_android_shots.py runs offline
(desktop machine, full core/ + mediapipe available), draws the boxes,
runs this same module's own render_composed() to build the final
composited file, and uploads the result to
"android/<stem>-composed.mp4" - compose_video() below prefers that
precomputed blob when one exists, and only falls back to its own
box-free on-the-fly render for a shot that script hasn't reached yet.

A phone's own trimmed clip (android/app/.../VideoDecoder.trimToMp4)
carries no rotation metadata (MediaMuxer.addTrack doesn't copy the
track header's rotation matrix, and trimToMp4 never calls
setOrientationHint itself) even though the sensor captured it
landscape - confirmed by pulling a real uploaded clip and looking at a
raw decoded frame, which comes out sideways. So this module always
applies a fixed 90-degree clockwise rotation (transpose=1), the same
correction CameraSession.open's own sensorOrientation handling would
have baked in if trimToMp4 preserved it. If a future phone/orientation
ever needs a different correction this hardcodes the wrong one - see
this module's own tests for the frame this was verified against."""

from __future__ import annotations

import logging
import re
import subprocess
import uuid
from pathlib import Path

import imageio_ffmpeg

from . import android_blobs

logger = logging.getLogger("shot_improvement_server")

SPECTROGRAM_HEIGHT = 200
PLAYHEAD_COLOR = "yellow"
PLAYHEAD_WIDTH = 3


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str]) -> None:
    proc = subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-2000:]}")


def probe_duration_s(path: Path) -> float:
    """Parses ffmpeg's own stderr "Duration: HH:MM:SS.xx" line - no
    ffprobe binary is bundled (imageio-ffmpeg ships ffmpeg only, not
    the whole suite), and a real ffprobe dependency isn't worth adding
    just to read one number."""
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not match:
        raise RuntimeError(f"could not read duration of {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def probe_size(path: Path) -> tuple[int, int]:
    """(width, height) as ffmpeg's own stream line reports them (the
    RAW decoded frame size - a phone's own trimmed clip has no rotation
    metadata at all, see this module's docstring, so this is always the
    landscape sensor size, never swapped for portrait). Different
    fps settings record at different sizes (FpsOptions.targetSizePxFor
    on the Android side), so callers must probe this per clip rather
    than assuming one fixed width - an earlier version of this module
    hardcoded 480 and broke on every shot recorded at another fps."""
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    match = re.search(r",\s*(\d+)x(\d+)[,\s]", proc.stderr)
    if not match:
        raise RuntimeError(f"could not read frame size of {path}")
    return int(match.group(1)), int(match.group(2))


def probe_frame_count(path: Path) -> int:
    """The real encoded frame count - deriving it as round(duration_s *
    fps) was off by one on a real composed file (122 real vs. 123
    computed, a boundary-rounding artifact), which would make the web
    player's "ruutu N/count" readout (matching MainActivity's own
    VideoFrameBox - spec 149) wrong by exactly the case a viewer is
    most likely to hit: the very last frame. A stream-copy null-muxer
    pass is cheap (no re-encode, no audio) and ffmpeg prints the real
    count in its own end-of-run stats line."""
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    matches = re.findall(r"frame=\s*(\d+)", proc.stderr)
    if not matches:
        raise RuntimeError(f"could not count frames of {path}")
    return int(matches[-1])


def probe_fps(path: Path) -> float:
    """Parses the "NN.NN fps" (or "NN fps") token ffmpeg prints on its
    video stream line - used by the web player's "one frame" step,
    which has no other way to know this clip's real frame rate."""
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    match = re.search(r"(\d+(?:\.\d+)?) fps", proc.stderr)
    return float(match.group(1)) if match else 30.0


def _spectrogram_only_size(duration_s: float) -> tuple[int, int]:
    # Audio-only shots (no camera that session) still get a playable
    # composed clip - the spectrogram image standing in for the missing
    # video, sized like a portrait video so the web player's layout
    # doesn't need a special case for it. Width scales gently with
    # duration so a longer clip's spectrogram isn't squeezed as tight
    # as a short one's.
    width = max(320, min(720, round(160 * duration_s)))
    return width, round(width * 4 / 3)


def _try_get_precomputed(stem: str, out_path: Path) -> Path | None:
    """A box-annotated composed video that tools/compose_android_shots.py
    has already rendered and uploaded to "android/<stem>-composed.mp4"
    - preferred over the plain on-the-fly render below whenever one
    exists, same blob-name convention as every other per-stem file."""
    blob = android_blobs._container().get_blob_client(f"{android_blobs.PREFIX}{stem}-composed.mp4")
    try:
        if not blob.exists():
            return None
        # A unique-per-call name (not just out_path + ".part") - two
        # concurrent requests for the same stem (e.g. an overlapping
        # page reload) must never write the same tmp path at once, or
        # one process's partial write can land in the other's finished
        # file before its own rename, producing a corrupt cached video
        # (see thumbnail()'s own tmp naming for the same reasoning).
        tmp = out_path.with_name(f"{out_path.name}.{uuid.uuid4().hex}.part")
        with open(tmp, "wb") as f:
            blob.download_blob().readinto(f)
        tmp.replace(out_path)
        return out_path
    except Exception:
        logger.warning("_try_get_precomputed: failed for %s", stem, exc_info=True)
        return None


def compose_video(stem: str) -> Path:
    """Returns the cached composited .mp4 for `stem`, building it on a
    cache miss. Prefers a precomputed, hand-position-annotated version
    if tools/compose_android_shots.py has already published one (see
    _try_get_precomputed) - that script runs MediaPipe pose detection
    per frame, ~50ms+ each, far too slow to run inline in a request
    here; this on-the-fly fallback skips hand boxes entirely so every
    shot still has SOME composed video available immediately after
    upload, not just the ones that script has gotten to yet.

    Always has an audio track; has real camera video on top only if
    the shot has one - otherwise the "video" band is just the
    spectrogram image itself, so the web player never needs two
    different playback code paths for the two cases."""
    out_path = android_blobs._CACHE_DIR / f"{stem}-composed.mp4"
    if out_path.exists():
        return out_path
    android_blobs._CACHE_DIR.mkdir(parents=True, exist_ok=True)

    precomputed = _try_get_precomputed(stem, out_path)
    if precomputed is not None:
        return precomputed

    audio_path = android_blobs.get_cached_path(stem, "wav")
    try:
        video_path: Path | None = android_blobs.get_cached_path(stem, "mp4")
    except Exception:
        video_path = None
    render_composed(video_path, audio_path, out_path, rotate=True)
    return out_path


def render_composed(video_path: Path | None, audio_path: Path, out_path: Path, *, rotate: bool) -> Path:
    """The shared ffmpeg pass: pads audio to match video_path's real
    duration (or audio_path's own, if there's no video), renders a
    static full-clip spectrogram with a moving "now" line, and vstacks
    it under video_path - rotating 90 degrees clockwise first if
    `rotate` (see this module's own docstring on why a phone's raw clip
    needs that). Used both by compose_video's on-the-fly fallback
    (rotate=True, the untouched trimmed clip) and by
    tools/compose_android_shots.py (rotate=False - it already rotated
    the frames itself while drawing hand boxes, so doing it again here
    would rotate the video twice)."""
    duration_s = probe_duration_s(video_path) if video_path is not None else probe_duration_s(audio_path)
    if video_path is not None:
        raw_w, raw_h = probe_size(video_path)
        # A phone's own recordings vary in size by fps setting
        # (FpsOptions.targetSizePxFor) - rotating swaps which dimension
        # becomes the composed video's width; an un-rotated (rotate=False)
        # input is already portrait, so its own width is already correct.
        width = raw_h if rotate else raw_w
        output_fps = probe_fps(video_path)
    else:
        width, _ = _spectrogram_only_size(duration_s)
        output_fps = 25.0  # no real source fps - just needs to be smooth enough for the moving line

    # Combining the camera video (an odd, often very high real fps - up to
    # 240) with the spectrogram overlay's own filter-graph timing (the
    # looped static image and the synthetic playhead-line video both
    # default to a plain 25fps) leaves the filter graph's frame PTSes
    # uneven, and passing that straight through to the muxer produced
    # genuinely invalid output - real composed clips this pipeline built
    # came out with non-monotonically-increasing DTS (confirmed via a
    # decode-only ffmpeg pass) and an absurd inferred timebase (380800!),
    # which desktop players and ffmpeg's own frame-grabbing tolerated but
    # Safari/iOS flatly refused to play at all. -r + -fps_mode cfr forces
    # the encoder to resample onto one clean, evenly-spaced timeline
    # instead of passing the filter graph's raw (buggy) timestamps
    # through - this is the actual fix, not a cosmetic one.
    cfr_args = ["-r", str(output_fps), "-fps_mode", "cfr"]

    # Unique per call, not just out_path's own name - two concurrent
    # composes of the SAME stem (e.g. a page reload firing before the
    # first request's ffmpeg pass finished) would otherwise have both
    # processes writing these same intermediate paths at once, and
    # ffmpeg has no locking of its own: the result is a genuinely
    # corrupt (partially-interleaved) output file that still passes
    # this function's own "did ffmpeg exit 0" check but fails to
    # decode in the browser. Each call gets its own tmp names instead;
    # the final tmp_out.replace(out_path) stays atomic either way.
    unique = uuid.uuid4().hex
    padded_audio = out_path.with_suffix(f".{unique}.padded.wav")
    spectrogram_png = out_path.with_suffix(f".{unique}.png")
    tmp_out = out_path.with_suffix(f".{unique}.tmp.mp4")
    # Bound before the try (not just before its first use) - the
    # finally block below unlinks it unconditionally, and an early
    # ffmpeg failure must not turn into a NameError masking the real
    # exception.
    filter_script = out_path.with_suffix(f".{unique}.filter.txt")
    try:
        _run(["-i", str(audio_path), "-af", f"apad=whole_dur={duration_s}", "-ar", "48000", str(padded_audio)])
        _run([
            "-i", str(padded_audio), "-lavfi",
            f"showspectrumpic=s={width}x{SPECTROGRAM_HEIGHT}:mode=combined:color=intensity:scale=log:legend=0",
            str(spectrogram_png),
        ])

        if video_path is not None:
            vrot_step = "[0:v]transpose=1[vrot];\n" if rotate else "[0:v]copy[vrot];\n"
            filter_script.write_text(
                vrot_step +
                "[2:v][3:v]overlay=x=(w2)*t/DUR:y=0:shortest=1[spec];\n"
                "[vrot][spec]vstack=inputs=2[outv]\n"
                .replace("w2", str(width - PLAYHEAD_WIDTH)).replace("DUR", str(duration_s))
            )
            args = [
                "-i", str(video_path), "-i", str(padded_audio),
                "-loop", "1", "-i", str(spectrogram_png),
                "-f", "lavfi", "-i", f"color={PLAYHEAD_COLOR}:s={PLAYHEAD_WIDTH}x{SPECTROGRAM_HEIGHT}:d={duration_s + 1}",
                "-filter_complex_script", str(filter_script),
                "-map", "[outv]", "-map", "1:a",
                "-t", str(duration_s), *cfr_args,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                str(tmp_out),
            ]
        else:
            _, height = _spectrogram_only_size(duration_s)
            filter_script.write_text(
                "[1:v][2:v]overlay=x=(w2)*t/DUR:y=0:shortest=1[outv]\n"
                .replace("w2", str(width - PLAYHEAD_WIDTH)).replace("DUR", str(duration_s))
            )
            args = [
                "-i", str(padded_audio),
                "-loop", "1", "-i", str(spectrogram_png),
                "-f", "lavfi", "-i", f"color={PLAYHEAD_COLOR}:s={PLAYHEAD_WIDTH}x{SPECTROGRAM_HEIGHT}:d={duration_s + 1}",
                "-filter_complex_script", str(filter_script),
                "-map", "[outv]", "-map", "0:a",
                "-t", str(duration_s), *cfr_args,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                str(tmp_out),
            ]
        _run(args)
        tmp_out.replace(out_path)
    finally:
        for tmp in (padded_audio, spectrogram_png, filter_script, tmp_out):
            tmp.unlink(missing_ok=True)
    return out_path


def thumbnail(stem: str) -> Path:
    """A single JPEG preview frame - the shot's own moment if it has
    video (same instant MainActivity.ShotFiles would show first when
    reviewing it), or the static spectrogram image if it doesn't.
    Cached indefinitely, same as compose_video - a stem's own files
    never change once uploaded."""
    out_path = android_blobs._CACHE_DIR / f"{stem}-thumb.jpg"
    if out_path.exists():
        return out_path
    android_blobs._CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        video_path: Path | None = android_blobs.get_cached_path(stem, "mp4")
    except Exception:
        video_path = None

    # Unique per call, same reasoning as render_composed()'s tmp
    # naming: two concurrent thumbnail requests for the same stem must
    # not both write ".tmp.jpg" at once.
    tmp_out = out_path.with_suffix(f".{uuid.uuid4().hex}.tmp.jpg")
    if video_path is not None:
        duration_s = probe_duration_s(video_path)
        # A third of the way in tends to land after the wind-up but
        # before the puck has left frame - a reasonable single-frame
        # stand-in without re-deriving the real shot timestamp here.
        _run(["-i", str(video_path), "-ss", str(duration_s / 3), "-vframes", "1", "-vf", "transpose=1", str(tmp_out)])
    else:
        audio_path = android_blobs.get_cached_path(stem, "wav")
        duration_s = probe_duration_s(audio_path)
        width, height = _spectrogram_only_size(duration_s)
        _run([
            "-i", str(audio_path), "-lavfi",
            f"showspectrumpic=s={width}x{height}:mode=combined:color=intensity:scale=log:legend=0",
            str(tmp_out),
        ])
    tmp_out.replace(out_path)
    return out_path
