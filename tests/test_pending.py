from pathlib import Path

from core.pending import PendingRecording, delete_pending, list_pending, write_meta


def _make_capture_dir(pending_dir: Path, timestamp: str) -> Path:
    capture_dir = pending_dir / timestamp
    (capture_dir / "raw").mkdir(parents=True)
    return capture_dir


def test_list_pending_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    assert list_pending(tmp_path) == []


def test_list_pending_missing_dir_returns_empty_list(tmp_path: Path) -> None:
    assert list_pending(tmp_path / "does-not-exist") == []


def test_write_meta_then_list_pending_round_trips_fields(tmp_path: Path) -> None:
    capture_dir = _make_capture_dir(tmp_path, "20260101120000")
    write_meta(
        capture_dir, frame_count=171, actual_fps=57.3, duration_s=3.0,
        video_name="c922 Pro Stream Webcam", audio_name="Mikrofoni (C922)",
    )

    items = list_pending(tmp_path)

    assert len(items) == 1
    item = items[0]
    assert item.timestamp == "20260101120000"
    assert item.frame_count == 171
    assert item.actual_fps == 57.3
    assert item.duration_s == 3.0
    assert item.video_name == "c922 Pro Stream Webcam"
    assert item.audio_name == "Mikrofoni (C922)"
    assert item.raw_dir == capture_dir / "raw"
    assert item.audio_path == capture_dir / "audio.wav"


def test_write_meta_round_trips_frame_times(tmp_path: Path) -> None:
    capture_dir = _make_capture_dir(tmp_path, "20260101120000")
    write_meta(
        capture_dir, frame_count=3, actual_fps=30.0, duration_s=0.1,
        video_name="c922 Pro Stream Webcam", audio_name=None,
        frame_times=[0.0, 0.033, 0.400],
    )

    item = list_pending(tmp_path)[0]

    assert item.frame_times == [0.0, 0.033, 0.400]


def test_write_meta_without_frame_times_defaults_to_empty_list(tmp_path: Path) -> None:
    """A pending/ entry saved before spec 097 (no frame_times key at
    all in its meta.json) must still load, falling back to the
    even-spacing path - never crash list_pending()."""
    capture_dir = _make_capture_dir(tmp_path, "20260101120000")
    write_meta(
        capture_dir, frame_count=1, actual_fps=1.0, duration_s=1.0,
        video_name="cam", audio_name=None,
    )

    item = list_pending(tmp_path)[0]

    assert item.frame_times == []


def test_write_meta_handles_no_audio_device(tmp_path: Path) -> None:
    capture_dir = _make_capture_dir(tmp_path, "20260101120000")
    write_meta(
        capture_dir, frame_count=10, actual_fps=30.0, duration_s=1.0,
        video_name="USB Camera", audio_name=None,
    )

    items = list_pending(tmp_path)

    assert items[0].audio_name is None


def test_list_pending_is_oldest_first(tmp_path: Path) -> None:
    for ts in ("20260103000000", "20260101000000", "20260102000000"):
        capture_dir = _make_capture_dir(tmp_path, ts)
        write_meta(capture_dir, frame_count=1, actual_fps=1.0, duration_s=1.0, video_name="cam", audio_name=None)

    items = list_pending(tmp_path)

    assert [item.timestamp for item in items] == ["20260101000000", "20260102000000", "20260103000000"]


def test_list_pending_skips_directory_with_no_meta(tmp_path: Path) -> None:
    _make_capture_dir(tmp_path, "20260101120000")  # never gets write_meta() - an interrupted capture

    assert list_pending(tmp_path) == []


def test_list_pending_skips_directory_with_malformed_meta(tmp_path: Path) -> None:
    capture_dir = _make_capture_dir(tmp_path, "20260101120000")
    (capture_dir / "meta.json").write_text("not valid json", encoding="utf-8")

    assert list_pending(tmp_path) == []


def test_list_pending_skips_directory_with_incomplete_meta(tmp_path: Path) -> None:
    capture_dir = _make_capture_dir(tmp_path, "20260101120000")
    (capture_dir / "meta.json").write_text('{"frame_count": 5}', encoding="utf-8")  # missing required fields

    assert list_pending(tmp_path) == []


def test_list_pending_ignores_non_directory_entries(tmp_path: Path) -> None:
    (tmp_path / "stray-file.txt").write_text("not a capture", encoding="utf-8")

    assert list_pending(tmp_path) == []


def test_delete_pending_removes_the_directory_tree(tmp_path: Path) -> None:
    capture_dir = _make_capture_dir(tmp_path, "20260101120000")
    write_meta(capture_dir, frame_count=1, actual_fps=1.0, duration_s=1.0, video_name="cam", audio_name=None)
    assert capture_dir.exists()

    delete_pending(capture_dir)

    assert not capture_dir.exists()


def test_delete_pending_on_missing_directory_does_not_raise(tmp_path: Path) -> None:
    delete_pending(tmp_path / "never-existed")  # must not raise


def test_created_label_formats_iso_timestamp() -> None:
    item = PendingRecording(
        dir_path=Path("x"), timestamp="t", frame_count=1, actual_fps=1.0, duration_s=1.0,
        video_name="cam", audio_name=None, created_at="2026-01-01T12:00:00",
    )
    assert item.created_label() == "2026-01-01 12:00:00"


def test_created_label_falls_back_to_raw_value_when_unparseable() -> None:
    item = PendingRecording(
        dir_path=Path("x"), timestamp="t", frame_count=1, actual_fps=1.0, duration_s=1.0,
        video_name="cam", audio_name=None, created_at="not-a-date",
    )
    assert item.created_label() == "not-a-date"
