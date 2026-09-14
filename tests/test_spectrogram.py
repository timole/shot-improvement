import numpy as np

from core.spectrogram import compute_spectrogram_image, with_playhead


def _test_tone(freq_hz=440.0, duration_s=1.0, sample_rate=44100):
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    return (np.sin(2 * np.pi * freq_hz * t) * 10000).astype("int16")


def test_compute_spectrogram_image_shape() -> None:
    audio = _test_tone()
    img = compute_spectrogram_image(audio, width=640, height=480)
    assert img.shape == (480, 640, 3)
    assert img.dtype == np.uint8


def test_compute_spectrogram_image_handles_very_short_audio() -> None:
    audio = np.array([1, 2, 3], dtype="int16")
    img = compute_spectrogram_image(audio, width=100, height=50)
    assert img.shape == (50, 100, 3)


def test_compute_spectrogram_image_handles_stereo() -> None:
    audio = np.stack([_test_tone(), _test_tone(880.0)], axis=1)
    img = compute_spectrogram_image(audio, width=200, height=100)
    assert img.shape == (100, 200, 3)


def test_with_playhead_draws_something() -> None:
    blank = np.zeros((50, 100, 3), dtype=np.uint8)
    result = with_playhead(blank, 0.5)
    assert result.any()  # a line got drawn
    assert not np.array_equal(result, blank)


def test_with_playhead_clamps_fraction() -> None:
    blank = np.zeros((50, 100, 3), dtype=np.uint8)
    # Out-of-range fractions shouldn't raise or index out of bounds.
    with_playhead(blank, -1.0)
    with_playhead(blank, 2.0)
