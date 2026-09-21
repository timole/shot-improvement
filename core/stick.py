"""Stick-bend visualisation (spec 134): draws the stick between the
player's two hands - a straight line while the stick is straight, an arc
while it bends.

The stick is held in both hands, so its two ends-of-interest are known:
the palm centres from core.pose (the middle of the yellow squares). Only
the curve between them has to be found. Colour is not a reliable cue on
this camera (at the fixed short exposure the light-green shaft came out
as a pale, low-saturation grey-blue, HSV about (110, 60-76, 100-120), and
as a *dark* line against bright sky) - what always distinguishes the shaft
is that it is a thin line, so the search uses a thin-line contrast
(a line brighter or darker than both flanks) instead: of the family of
arcs that start and end at the two hands (a parabola with sideways bulge
`d` in the middle), the one collecting the strongest thin-line response
along its length wins. A straight line is preferred unless an arc is
clearly better.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

from .pose import PalmBox

STICK_COLOR_BGR = (144, 238, 144)  # light green
STICK_OUTLINE_BGR = (0, 0, 0)
STICK_THICKNESS = 2
# A shaft is ~2-4 px wide at 640x360: light smoothing, flanks 4 px either side.
LINE_SMOOTH_SIGMA = 0.8
FLANK_PX = 4.0
# The bulge is searched up to this fraction of the hand-to-hand distance.
MAX_BEND_FRACTION = 0.25
BEND_STEPS = 41
CURVE_SAMPLES = 40
# Ends are excluded from scoring: the hands and gloves are clutter.
END_MARGIN = 0.12
# An arc must beat the straight line's mean response by this factor,
# and bend at least this many pixels, to count as bent.
BEND_RESPONSE_GAIN = 1.25
# ...and the best arc must actually contain a line (mean contrast, 0-255 units).
MIN_LINE_CONTRAST = 6.0
MIN_BEND_PX = 1.5
MIN_HAND_DISTANCE_PX = 12.0


@dataclass(frozen=True)
class StickArc:
    hand_a: tuple[float, float]
    hand_b: tuple[float, float]
    bend_px: float  # signed sideways bulge of the arc's middle (0 = straight)
    bent: bool

    def points(self, samples: int = CURVE_SAMPLES) -> np.ndarray:
        return _curve_points(self.hand_a, self.hand_b, self.bend_px, samples)


def _curve_points(a: tuple[float, float], b: tuple[float, float], bend_px: float, samples: int) -> np.ndarray:
    """Points from a to b along the parabola with sideways bulge bend_px
    at the middle (bulge = 4 * t * (1 - t) * bend_px)."""
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    chord = b_arr - a_arr
    length = float(np.hypot(*chord))
    normal = np.array([-chord[1], chord[0]]) / length
    t = np.linspace(0.0, 1.0, samples)[:, None]
    return a_arr + t * chord + 4.0 * t * (1.0 - t) * bend_px * normal


def smoothed_luminance(frame_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return cv2.GaussianBlur(gray, (0, 0), LINE_SMOOTH_SIGMA)


def _sample(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    h, w = image.shape
    xs = np.clip(points[:, 0], 0, w - 1).astype(np.float32)
    ys = np.clip(points[:, 1], 0, h - 1).astype(np.float32)
    return cv2.remap(image, xs.reshape(1, -1), ys.reshape(1, -1), cv2.INTER_LINEAR).reshape(-1)


def line_contrast(luma: np.ndarray, points: np.ndarray) -> float:
    """Mean thin-line contrast along a curve: at each point compare the
    luminance on the curve with the luminance FLANK_PX to either side (across
    the curve's local direction). A thin line - bright or dark - differs from
    BOTH flanks in the same direction; a step edge (torso, horizon) equals
    one of them and scores ~0, flat areas score ~0."""
    tangent = np.gradient(points, axis=0)
    norm = np.hypot(tangent[:, 0], tangent[:, 1])[:, None]
    normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1) / np.maximum(norm, 1e-6)
    centre = _sample(luma, points)
    left = _sample(luma, points - FLANK_PX * normal)
    right = _sample(luma, points + FLANK_PX * normal)
    bright = np.minimum(centre - left, centre - right)
    dark = np.minimum(left - centre, right - centre)
    return float(np.maximum(np.maximum(bright, dark), 0.0).mean())


def fit_stick_arc(
    luma: np.ndarray, hand_a: tuple[float, float], hand_b: tuple[float, float],
) -> Optional[StickArc]:
    """The arc (or straight line) between the hands that best follows a thin
    line in the smoothed luminance image; None if the hands are too close
    together to define a stick."""
    distance = float(np.hypot(hand_b[0] - hand_a[0], hand_b[1] - hand_a[1]))
    if distance < MIN_HAND_DISTANCE_PX:
        return None
    max_bend = MAX_BEND_FRACTION * distance
    lo = int(CURVE_SAMPLES * END_MARGIN)
    hi = CURVE_SAMPLES - lo

    def score(bend_px: float) -> float:
        points = _curve_points(hand_a, hand_b, bend_px, CURVE_SAMPLES)[lo:hi]
        return line_contrast(luma, points)

    straight = score(0.0)
    best_bend, best_score = 0.0, straight
    for bend in np.linspace(-max_bend, max_bend, BEND_STEPS):
        s = score(float(bend))
        if s > best_score:
            best_bend, best_score = float(bend), s
    bent = (
        abs(best_bend) >= MIN_BEND_PX
        and best_score >= straight * BEND_RESPONSE_GAIN
        and best_score >= MIN_LINE_CONTRAST
    )
    return StickArc(hand_a, hand_b, best_bend if bent else 0.0, bent)


def draw_stick(frame_bgr: np.ndarray, arc: StickArc) -> None:
    pts = np.round(arc.points()).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(frame_bgr, [pts], False, STICK_OUTLINE_BGR, STICK_THICKNESS + 2, cv2.LINE_AA)
    cv2.polylines(frame_bgr, [pts], False, STICK_COLOR_BGR, STICK_THICKNESS, cv2.LINE_AA)


def hands_from_boxes(boxes: list[PalmBox]) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
    """The two hands' centres, upper one first; None unless both are found."""
    if len(boxes) < 2:
        return None
    a, b = sorted(boxes[:2], key=lambda box: box.cy)
    return (a.cx, a.cy), (b.cx, b.cy)


HANDS_SUFFIX = "-hands.json"


def hands_sidecar_path(clip_path: Path) -> Path:
    """recordings/shot-improvement-<ts>[-annotated].mp4 ->
    recordings/shot-improvement-<ts>-hands.json"""
    stem = clip_path.name
    for suffix in ("-annotated.mp4", ".mp4"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return clip_path.with_name(stem + HANDS_SUFFIX)


def write_hands_sidecar(path: Path, frame_times: Sequence[float], hands: Sequence[Optional[tuple]]) -> None:
    """Per captured frame, the two hand centres the yellow boxes were drawn
    at (upper hand first), or null - written by the annotation pass so
    "Show stick" can use exactly the boxes' positions."""
    payload = {
        "times": [round(float(t), 4) for t in frame_times],
        "hands": [None if h is None else [[round(p[0], 1), round(p[1], 1)] for p in h] for h in hands],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class HandsTrack:
    """The sidecar's hand centres, looked up by time in seconds."""

    def __init__(self, times: list[float], hands: list) -> None:
        self._times = times
        self._hands = hands

    @classmethod
    def load(cls, path: Path) -> Optional["HandsTrack"]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls([float(t) for t in data["times"]], data["hands"])
        except (OSError, ValueError, KeyError):
            return None

    def at(self, t_s: float) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
        if not self._times:
            return None
        i = bisect.bisect_left(self._times, t_s)
        if i > 0 and (i == len(self._times) or abs(self._times[i - 1] - t_s) <= abs(self._times[i] - t_s)):
            i -= 1
        pair = self._hands[i] if i < len(self._hands) else None
        if pair is None:
            return None
        return (pair[0][0], pair[0][1]), (pair[1][0], pair[1][1])


class StickAnnotator:
    """Draws the stick onto single frames, in any order (playback,
    scrubbing, stepping). Uses its own pose landmarker in IMAGE mode - each
    frame is analysed on its own, so there's no clock/ordering requirement
    like the VIDEO-mode PoseDetector has. The hand-visibility bar is lower
    than the yellow boxes' (0.5): a hand gripping a stick is often only
    partly visible (0.3-0.4 on real frames) but its position is still good."""

    MIN_HAND_VISIBILITY = 0.2

    def __init__(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        from .pose import MODEL_PATH, ensure_model_downloaded

        self._mp = mp
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model_downloaded(MODEL_PATH))),
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)

    def hands(self, frame_bgr: np.ndarray) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
        from .pose import palm_boxes_from_landmarks

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)
        h, w = frame_bgr.shape[:2]
        boxes = palm_boxes_from_landmarks(result.pose_landmarks, w, h, self.MIN_HAND_VISIBILITY)
        return hands_from_boxes(boxes)

    def find(self, frame_bgr: np.ndarray, hands=None) -> Optional[StickArc]:
        """The stick (arc or straight line) between the hands in this
        frame - `hands` if given (e.g. from the annotation pass's sidecar),
        else found by pose detection; None if both hands weren't found."""
        if hands is None:
            hands = self.hands(frame_bgr)
        if hands is None:
            return None
        return fit_stick_arc(smoothed_luminance(frame_bgr), *hands)

    def annotate(self, frame_bgr: np.ndarray) -> Optional[StickArc]:
        """Draws the stick onto frame_bgr in place; returns the arc, or
        None (nothing drawn) if both hands weren't found."""
        arc = self.find(frame_bgr)
        if arc is not None:
            draw_stick(frame_bgr, arc)
        return arc

    def close(self) -> None:
        self._landmarker.close()
