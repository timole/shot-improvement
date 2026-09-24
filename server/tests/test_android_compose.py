"""Integration tests for server/android_compose.py's ffmpeg pipeline
(spec 149) - unlike the mocked tests in test_android_blobs.py, these
run the REAL bundled ffmpeg (imageio-ffmpeg) against small synthetic
clips generated with ffmpeg's own lavfi test sources, so no Azure
access and no real phone recording is needed. Slower than the rest of
the suite (each case shells out to ffmpeg a handful of times) but still
a couple of seconds total for these tiny (1-2s) inputs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from server import android_compose


def _make_video(path: Path, width: int, height: int, duration_s: float = 1.0, fps: int = 30) -> None:
    subprocess.run(
        [
            android_compose._ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={duration_s}",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )


def _make_audio(path: Path, duration_s: float = 0.6) -> None:
    subprocess.run(
        [
            android_compose._ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
            "-ar", "48000", "-ac", "1", str(path),
        ],
        check=True,
    )


def test_probe_duration_s_reads_a_real_file(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    _make_audio(audio, duration_s=0.6)
    assert android_compose.probe_duration_s(audio) == pytest.approx(0.6, abs=0.05)


def test_probe_frame_count_reads_the_real_encoded_count(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    # 12.5 frames' worth at 25fps - real encoders round to a whole
    # frame, so this also guards against round(duration*fps)-style
    # off-by-one drift (see probe_frame_count's own docstring).
    _make_video(video, 64, 48, duration_s=0.5, fps=25)
    assert android_compose.probe_frame_count(video) == 13


def test_probe_size_reads_the_raw_landscape_frame(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    _make_video(video, 320, 240)
    assert android_compose.probe_size(video) == (320, 240)


def test_render_composed_output_has_clean_monotonic_timestamps(tmp_path: Path) -> None:
    # A real phone's fps is never a round number (240.28..., not 240) -
    # combining that with the spectrogram overlay's own filter-graph
    # timing (the looped static image and the synthetic playhead line
    # both default to a plain 25fps) left the unforced output with
    # non-monotonically-increasing DTS and an absurd inferred timebase
    # (380800!) on real shots - ffmpeg's own frame-grabbing and desktop
    # players tolerated it, but Safari/iOS flatly refused to play the
    # file at all. render_composed must force clean CFR output (see its
    # own cfr_args) so this never regresses.
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    _make_video(video, 320, 240, duration_s=1.0, fps="240283/1000")
    _make_audio(audio, duration_s=0.6)

    android_compose.render_composed(video, audio, out, rotate=True)

    decode = subprocess.run(
        [android_compose._ffmpeg(), "-hide_banner", "-i", str(out), "-loglevel", "error", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    assert decode.stderr == ""  # any decoder warning (e.g. non-monotonic DTS) lands here
    fps = android_compose.probe_fps(out)
    assert 1 <= fps <= 1000  # sane, not a filter-graph-timebase artifact like 380800


def test_render_composed_rotates_and_vstacks_with_audio(tmp_path: Path) -> None:
    # A landscape (width > height) source, like a phone's own raw clip -
    # render_composed(rotate=True) must swap it to portrait before
    # stacking the spectrogram underneath, or vstack itself would raise
    # (mismatched widths) exactly like the real bug this test guards
    # against (a shot recorded at a non-480-wide fps setting once broke
    # tools/compose_android_shots.py's whole batch - see this module's
    # own probe_size docstring).
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    _make_video(video, 320, 240, duration_s=1.0)
    _make_audio(audio, duration_s=0.6)

    android_compose.render_composed(video, audio, out, rotate=True)

    assert out.is_file() and out.stat().st_size > 0
    width, height = android_compose.probe_size(out)
    assert width == 240  # the rotated (landscape height -> portrait width) size
    assert height == 320 + android_compose.SPECTROGRAM_HEIGHT  # rotated landscape width -> portrait height, + the spectrogram band
    assert android_compose.probe_duration_s(out) == pytest.approx(1.0, abs=0.1)


def test_render_composed_without_video_uses_spectrogram_as_the_picture(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    _make_audio(audio, duration_s=0.6)

    android_compose.render_composed(None, audio, out, rotate=True)

    assert out.is_file() and out.stat().st_size > 0
    assert android_compose.probe_duration_s(out) == pytest.approx(0.6, abs=0.1)


def test_compose_video_prefers_a_precomputed_blob(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """compose_video must not run its own ffmpeg render at all when
    tools/compose_android_shots.py has already published
    "android/<stem>-composed.mp4" - it should just download and serve
    that (hand-position-annotated) version instead."""
    from server import android_blobs

    stem = "shot-20260101000000-1"
    monkeypatch.setattr(android_blobs, "_CACHE_DIR", tmp_path)

    class FakeBlob:
        def exists(self) -> bool:
            return True

        def download_blob(self):
            class _Downloader:
                def readinto(self, f):
                    f.write(b"precomputed-bytes")

            return _Downloader()

    class FakeContainer:
        def get_blob_client(self, name: str):
            assert name == f"android/{stem}-composed.mp4"
            return FakeBlob()

    monkeypatch.setattr(android_blobs, "_container", lambda: FakeContainer())

    def fail_if_called(*args, **kwargs):
        raise AssertionError("render_composed must not run when a precomputed blob exists")

    monkeypatch.setattr(android_compose, "render_composed", fail_if_called)

    out = android_compose.compose_video(stem)
    assert out.read_bytes() == b"precomputed-bytes"


def test_render_composed_video_already_rotated_is_not_rotated_twice(tmp_path: Path) -> None:
    # Simulates tools/compose_android_shots.py's own call shape: a
    # video that's ALREADY portrait (width < height) because it was
    # rotated during hand-box drawing - rotate=False must leave it as
    # is, not rotate an already-upright frame onto its side.
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    _make_video(video, 240, 320, duration_s=1.0)  # already portrait
    _make_audio(audio, duration_s=0.6)

    android_compose.render_composed(video, audio, out, rotate=False)

    width, _ = android_compose.probe_size(out)
    assert width == 240  # unchanged - not swapped to 320
