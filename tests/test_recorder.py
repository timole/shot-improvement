from datetime import datetime

from core.recorder import timestamp_for_filename


def test_timestamp_for_filename_format() -> None:
    dt = datetime(2026, 9, 11, 11, 22, 7)
    assert timestamp_for_filename(dt) == "20260911112207"


def test_timestamp_for_filename_pads_single_digits() -> None:
    dt = datetime(2026, 1, 2, 3, 4, 5)
    assert timestamp_for_filename(dt) == "20260102030405"
