import numpy as np

from core.playback import format_time, resample_for_speed


def test_format_time_basic() -> None:
    assert format_time(0) == "0:00.0"
    assert format_time(3.24) == "0:03.2"
    assert format_time(63.4) == "1:03.4"


def test_format_time_clamps_negative() -> None:
    assert format_time(-5) == "0:00.0"


def test_resample_for_speed_identity_at_1x() -> None:
    audio = np.array([1, 2, 3, 4, 5], dtype="int16")
    result = resample_for_speed(audio, 1.0)
    assert result is audio


def test_resample_for_speed_2x_halves_length() -> None:
    audio = np.arange(100, dtype="int16")
    result = resample_for_speed(audio, 2.0)
    assert abs(len(result) - 50) <= 1
    assert result.dtype == audio.dtype


def test_resample_for_speed_half_x_doubles_length() -> None:
    audio = np.arange(100, dtype="int16")
    result = resample_for_speed(audio, 0.5)
    assert abs(len(result) - 200) <= 1


def test_resample_for_speed_preserves_stereo_shape() -> None:
    audio = np.stack([np.arange(100), np.arange(100) * 2], axis=1).astype("int16")
    result = resample_for_speed(audio, 2.0)
    assert result.ndim == 2
    assert result.shape[1] == 2


def test_resample_for_speed_empty_audio() -> None:
    audio = np.array([], dtype="int16")
    result = resample_for_speed(audio, 2.0)
    assert len(result) == 0
