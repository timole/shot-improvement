from datetime import datetime
from pathlib import Path

from core.recorder import FRAME_FILE_EXTENSION, _write_concat_list, timestamp_for_filename


def test_timestamp_for_filename_format() -> None:
    dt = datetime(2026, 9, 11, 11, 22, 7)
    assert timestamp_for_filename(dt) == "20260911112207"


def test_timestamp_for_filename_pads_single_digits() -> None:
    dt = datetime(2026, 1, 2, 3, 4, 5)
    assert timestamp_for_filename(dt) == "20260102030405"


def test_write_concat_list_uses_real_gaps_between_frames(tmp_path: Path) -> None:
    """Spec 097: each frame's listed duration must be the REAL measured
    gap to the next frame's capture time, not an assumed constant -
    this is the whole point of the fix (see core.recorder's module
    docstring)."""
    frame_times = [0.0, 0.030, 0.400, 0.410]  # a stall between frames 1 and 2

    list_path = _write_concat_list(tmp_path, frame_times)

    text = list_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert f"file 'frame_000000.{FRAME_FILE_EXTENSION}'" in lines
    assert "duration 0.030000" in text  # gap 0->1
    assert "duration 0.370000" in text  # the stall, gap 1->2
    assert "duration 0.010000" in text  # gap 2->3


def test_write_concat_list_repeats_last_file_with_no_trailing_duration(tmp_path: Path) -> None:
    """ffmpeg's concat demuxer ignores the FINAL duration directive - the
    documented workaround (used here) is a trailing repeat of the last
    file with no duration line after it."""
    frame_times = [0.0, 0.033, 0.066]

    list_path = _write_concat_list(tmp_path, frame_times)

    lines = list_path.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == f"file 'frame_000002.{FRAME_FILE_EXTENSION}'"
    # and it's genuinely last - no duration follows it
    assert "duration" not in lines[-1]


def test_write_concat_list_handles_a_single_frame(tmp_path: Path) -> None:
    list_path = _write_concat_list(tmp_path, [0.0])

    lines = list_path.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "ffconcat version 1.0",
        f"file 'frame_000000.{FRAME_FILE_EXTENSION}'",
        "duration 1.000000",
        f"file 'frame_000000.{FRAME_FILE_EXTENSION}'",
    ]
