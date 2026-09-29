"""Conservative camera-plane hand speed around puck contact (not 3D speed)."""
from __future__ import annotations

import numpy as np


def estimate_speed(samples, shot_t, height_m, hand):
    """Least-squares velocity in the final 50 ms, with visibility/jitter gates.

    Pixel scale uses the median visible head-to-ankle articulated body length
    before contact. Perspective/foreshortening remain systematic uncertainty.
    Missing/occluded hands yield no estimate rather than an invented value.
    """
    if not height_m or not 0.8 <= height_m <= 2.3 or hand not in ("left hand", "right hand"):
        return None
    scales = [s["body_px"] for s in samples if shot_t - .5 <= s["t"] <= shot_t and s.get("body_px", 0) > 30]
    points = [(s["t"], *s[hand]) for s in samples if shot_t - .05 <= s["t"] <= shot_t and hand in s]
    if len(scales) < 5 or len(points) < 7:
        return None
    a = np.asarray(points)
    if a[-1, 0] - a[0, 0] < .03 or shot_t - a[-1, 0] > .012:
        return None
    if np.max(np.diff(a[:, 0])) > .013:
        return None
    t = a[:, 0] - np.mean(a[:, 0])
    centered = a[:, 1:] - np.mean(a[:, 1:], axis=0)
    velocity = (t[:, None] * centered).sum(axis=0) / (t @ t)
    residual = np.sqrt(np.mean(np.sum((centered - t[:, None] * velocity)**2, axis=1)))
    displacement = np.linalg.norm(velocity) * (a[-1, 0] - a[0, 0])
    if residual > max(2.5, displacement * .3):
        return None
    speed = float(np.linalg.norm(velocity) * height_m / np.median(scales) * 3.6)
    return round(speed, 1) if 0 <= speed <= 100 else None


def body_pixels(landmarks, width, height):
    """Articulated ankle/knee/hip/shoulder/nose plus head-top allowance."""
    lengths = []
    for chain in ((27, 25, 23, 11, 0), (28, 26, 24, 12, 0)):
        if any(getattr(landmarks[i], "visibility", 0) < .7 for i in chain):
            continue
        pts = np.array([(landmarks[i].x * width, landmarks[i].y * height) for i in chain])
        lengths.append(float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum() * 1.08))
    return float(np.median(lengths)) if lengths else 0
