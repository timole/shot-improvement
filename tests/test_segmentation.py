import numpy as np

from core.segmentation import apply_person_mask


def test_apply_person_mask_keeps_person_pixels() -> None:
    frame = np.full((4, 4, 3), 255, dtype=np.uint8)
    # category 0 = person (left half), non-zero = background (right half)
    mask = np.array([[0, 0, 1, 1]] * 4, dtype=np.uint8)
    out = apply_person_mask(frame, mask)
    assert (out[:, :2] == 255).all()  # person side untouched
    assert (out[:, 2:] == 0).all()  # background side blacked out


def test_apply_person_mask_all_background() -> None:
    frame = np.full((4, 4, 3), 200, dtype=np.uint8)
    mask = np.ones((4, 4), dtype=np.uint8)
    out = apply_person_mask(frame, mask)
    assert (out == 0).all()


def test_apply_person_mask_all_person() -> None:
    frame = np.full((4, 4, 3), 200, dtype=np.uint8)
    mask = np.zeros((4, 4), dtype=np.uint8)
    out = apply_person_mask(frame, mask)
    assert np.array_equal(out, frame)


def test_apply_person_mask_does_not_mutate_input() -> None:
    frame = np.full((2, 2, 3), 100, dtype=np.uint8)
    mask = np.array([[1, 0], [0, 1]], dtype=np.uint8)
    apply_person_mask(frame, mask)
    assert (frame == 100).all()
