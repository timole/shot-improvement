from core.devices import find_audio_device_index, find_video_device_index, first_non_virtual_video_device_index


def test_find_video_device_index_matches_case_insensitively() -> None:
    devices = ["USB Camera", "Logitech BRIO", "OBS Virtual Camera"]
    assert find_video_device_index("logitech", devices) == 1


def test_find_video_device_index_no_match_returns_none() -> None:
    devices = ["USB Camera", "OBS Virtual Camera"]
    assert find_video_device_index("logitech", devices) is None


def test_first_non_virtual_video_device_index_skips_virtual_cameras() -> None:
    devices = ["OBS Virtual Camera", "USB Camera"]
    assert first_non_virtual_video_device_index(devices) == 1


def test_first_non_virtual_video_device_index_falls_back_to_zero_if_all_virtual() -> None:
    devices = ["OBS Virtual Camera"]
    assert first_non_virtual_video_device_index(devices) == 0


def test_first_non_virtual_video_device_index_none_when_no_devices() -> None:
    assert first_non_virtual_video_device_index([]) is None


def test_find_audio_device_index_matches_input_devices_only() -> None:
    devices = [
        {"name": "Jabra Speak (output)", "max_input_channels": 0},
        {"name": "Jabra Speak (input)", "max_input_channels": 2},
        {"name": "Realtek Mic", "max_input_channels": 2},
    ]
    assert find_audio_device_index("jabra", devices) == 1


def test_find_audio_device_index_no_match_returns_none() -> None:
    devices = [{"name": "Realtek Mic", "max_input_channels": 2}]
    assert find_audio_device_index("jabra", devices) is None
