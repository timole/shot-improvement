from dataclasses import dataclass

import numpy as np

from core.pose import draw_palm_boxes, palm_boxes_from_landmarks


@dataclass
class FakeLandmark:
    x: float
    y: float
    visibility: float = 1.0


def _make_pose(wrist_xy, pinky_xy, index_xy, visibility=1.0):
    """33 landmarks, only the ones palm_boxes_from_landmarks reads are
    meaningful - the rest are filler, marked not-visible so they don't
    themselves form a spurious second palm box."""
    landmarks = [FakeLandmark(0.0, 0.0, visibility=0.0) for _ in range(33)]
    landmarks[15] = FakeLandmark(*wrist_xy, visibility)
    landmarks[17] = FakeLandmark(*pinky_xy, visibility)
    landmarks[19] = FakeLandmark(*index_xy, visibility)
    return landmarks


def test_palm_box_is_the_average_of_wrist_pinky_and_index_knuckle() -> None:
    pose = _make_pose(wrist_xy=(0.3, 0.6), pinky_xy=(0.36, 0.54), index_xy=(0.33, 0.51))
    boxes = palm_boxes_from_landmarks([pose], frame_width=100, frame_height=200)

    assert len(boxes) == 1
    box = boxes[0]
    assert box.label == "left hand"
    assert box.cx == (0.3 + 0.36 + 0.33) / 3 * 100
    assert box.cy == (0.6 + 0.54 + 0.51) / 3 * 200


def test_low_visibility_landmark_is_skipped() -> None:
    pose = _make_pose(wrist_xy=(0.3, 0.6), pinky_xy=(0.36, 0.54), index_xy=(0.33, 0.51), visibility=0.1)
    boxes = palm_boxes_from_landmarks([pose], frame_width=100, frame_height=200)
    assert boxes == []


def test_no_detected_people_gives_no_boxes() -> None:
    assert palm_boxes_from_landmarks([], frame_width=100, frame_height=200) == []


def test_both_hands_produce_two_boxes() -> None:
    landmarks = [FakeLandmark(0.0, 0.0) for _ in range(33)]
    landmarks[15] = FakeLandmark(0.2, 0.5)
    landmarks[17] = FakeLandmark(0.2, 0.5)
    landmarks[19] = FakeLandmark(0.2, 0.5)
    landmarks[16] = FakeLandmark(0.8, 0.5)
    landmarks[18] = FakeLandmark(0.8, 0.5)
    landmarks[20] = FakeLandmark(0.8, 0.5)

    boxes = palm_boxes_from_landmarks([landmarks], frame_width=100, frame_height=100)

    labels = {box.label for box in boxes}
    assert labels == {"left hand", "right hand"}


def test_draw_palm_boxes_does_not_crash_and_modifies_the_frame() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    pose = _make_pose(wrist_xy=(0.5, 0.5), pinky_xy=(0.5, 0.5), index_xy=(0.5, 0.5))
    boxes = palm_boxes_from_landmarks([pose], frame_width=100, frame_height=100)

    draw_palm_boxes(frame, boxes)

    assert frame.any()  # something green got drawn, not still all zeros
