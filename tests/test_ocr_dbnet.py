"""DB post-processing tests on a synthetic probability map — no model needed.

The map is drawn by hand, so the expected boxes are known exactly. The gap test
covers the defect that made two columns on one line merge into a single box.
"""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("pyclipper")

from extractor.ocr.dbnet_post import boxes_from_bitmap, sort_reading_order  # noqa: E402

MAP_HEIGHT, MAP_WIDTH = 200, 400


def _blank_map() -> np.ndarray:
    return np.zeros((MAP_HEIGHT, MAP_WIDTH), dtype=np.float32)


def _add_bar(prob_map: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> None:
    """Paint a confident rectangular text region into the map."""
    prob_map[y0:y1, x0:x1] = 0.95


def test_empty_map_finds_nothing():
    assert boxes_from_bitmap(_blank_map(), (MAP_HEIGHT, MAP_WIDTH)) == []


def test_one_region_becomes_one_box():
    prob_map = _blank_map()
    _add_bar(prob_map, 40, 60, 300, 90)
    boxes = boxes_from_bitmap(prob_map, (MAP_HEIGHT, MAP_WIDTH))
    assert len(boxes) == 1
    assert boxes[0].shape == (4, 2)


def test_two_regions_on_one_line_stay_two_boxes():
    # The unclip step expands boxes; a 60 px gap must survive it, otherwise a
    # two-column heading is read as one run-on line.
    prob_map = _blank_map()
    _add_bar(prob_map, 20, 60, 150, 90)
    _add_bar(prob_map, 210, 60, 340, 90)
    boxes = boxes_from_bitmap(prob_map, (MAP_HEIGHT, MAP_WIDTH))
    assert len(boxes) == 2


def test_low_probability_regions_are_rejected():
    prob_map = _blank_map()
    prob_map[60:90, 40:300] = 0.31  # above the binarize threshold, below the box score
    assert boxes_from_bitmap(prob_map, (MAP_HEIGHT, MAP_WIDTH)) == []


def test_regions_thinner_than_the_minimum_are_rejected():
    prob_map = _blank_map()
    _add_bar(prob_map, 40, 60, 300, 61)
    assert boxes_from_bitmap(prob_map, (MAP_HEIGHT, MAP_WIDTH)) == []


def test_boxes_are_scaled_to_the_original_image_size():
    prob_map = _blank_map()
    _add_bar(prob_map, 40, 60, 300, 90)
    boxes = boxes_from_bitmap(prob_map, (MAP_HEIGHT * 2, MAP_WIDTH * 2))
    assert boxes[0][:, 0].max() > MAP_WIDTH
    assert boxes[0][:, 0].max() <= MAP_WIDTH * 2


def test_corners_are_ordered_clockwise_from_the_top_left():
    prob_map = _blank_map()
    _add_bar(prob_map, 40, 60, 300, 100)
    box = boxes_from_bitmap(prob_map, (MAP_HEIGHT, MAP_WIDTH))[0]
    top_left, top_right, bottom_right, bottom_left = box
    assert top_left[0] < top_right[0]
    assert top_left[1] < bottom_left[1]
    assert bottom_right[0] > bottom_left[0]


# ---------------------------------------------------------------------------
# Reading order
# ---------------------------------------------------------------------------

def _box(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)


def test_reading_order_is_top_to_bottom_then_left_to_right():
    lower = _box(0, 100, 100, 130)
    right = _box(200, 10, 300, 40)
    left = _box(0, 10, 100, 40)
    ordered = sort_reading_order([lower, right, left])
    assert [b[0, 0] for b in ordered] == [0, 200, 0]
    assert [b[0, 1] for b in ordered] == [10, 10, 100]


def test_reading_order_of_nothing_is_nothing():
    assert sort_reading_order([]) == []
