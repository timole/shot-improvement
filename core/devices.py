"""Camera/microphone selection by (partial, case-insensitive) name.

Windows has no reliable "friendly name" lookup in OpenCV itself, so
video devices are enumerated via DirectShow (pygrabber) instead - its
device order matches the index cv2.VideoCapture(index, cv2.CAP_DSHOW)
expects. Audio devices use sounddevice's own enumeration.
"""

from __future__ import annotations

import sounddevice as sd
from pygrabber.dshow_graph import FilterGraph


def list_video_devices() -> list[str]:
    return FilterGraph().get_input_devices()


def find_video_device_index(name_substring: str, devices: list[str] | None = None) -> int | None:
    devices = list_video_devices() if devices is None else devices
    needle = name_substring.lower()
    for i, name in enumerate(devices):
        if needle in name.lower():
            return i
    return None


def first_non_virtual_video_device_index(devices: list[str] | None = None) -> int | None:
    """Fallback when no name match is found: the first device whose name
    doesn't look like a virtual camera (e.g. "OBS Virtual Camera")."""
    devices = list_video_devices() if devices is None else devices
    for i, name in enumerate(devices):
        if "virtual" not in name.lower():
            return i
    return 0 if devices else None


def list_input_audio_devices() -> list[dict]:
    return [d for d in sd.query_devices() if d["max_input_channels"] > 0]


def find_audio_device_index(name_substring: str, devices: list[dict] | None = None) -> int | None:
    devices = sd.query_devices() if devices is None else devices
    needle = name_substring.lower()
    for i, info in enumerate(devices):
        if info["max_input_channels"] > 0 and needle in info["name"].lower():
            return i
    return None


def list_output_audio_devices() -> list[dict]:
    return [d for d in sd.query_devices() if d["max_output_channels"] > 0]


def find_output_audio_device_index(name_substring: str, devices: list[dict] | None = None) -> int | None:
    devices = sd.query_devices() if devices is None else devices
    needle = name_substring.lower()
    for i, info in enumerate(devices):
        if info["max_output_channels"] > 0 and needle in info["name"].lower():
            return i
    return None
