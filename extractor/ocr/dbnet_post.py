"""DB detection post-processing: probability map to text quadrilaterals.

This is the part of the pipeline with the most tuning in it, so it lives alone
and is unit-tested against a synthetic probability map. The unclip step matters
most: without it, adjacent columns on one line merge into a single box and two
separate phrases come back glued together.
"""
from __future__ import annotations

import cv2
import numpy as np
import pyclipper

BINARY_THRESHOLD = 0.3
BOX_SCORE_THRESHOLD = 0.5
UNCLIP_RATIO = 1.5
MIN_BOX_SIDE = 3
MAX_CANDIDATES = 1000


def _box_score(prob_map: np.ndarray, box: np.ndarray) -> float:
    """Mean probability inside *box* — how confident the map is about it."""
    height, width = prob_map.shape
    x_min = max(0, min(int(np.floor(box[:, 0].min())), width - 1))
    x_max = max(0, min(int(np.ceil(box[:, 0].max())), width - 1))
    y_min = max(0, min(int(np.floor(box[:, 1].min())), height - 1))
    y_max = max(0, min(int(np.ceil(box[:, 1].max())), height - 1))

    mask = np.zeros((y_max - y_min + 1, x_max - x_min + 1), dtype=np.uint8)
    shifted = box.copy()
    shifted[:, 0] -= x_min
    shifted[:, 1] -= y_min
    cv2.fillPoly(mask, [shifted.astype(np.int32)], 1)
    region = prob_map[y_min:y_max + 1, x_min:x_max + 1]
    if mask.sum() == 0:
        return 0.0
    return float(cv2.mean(region, mask)[0])


def _unclip(box: np.ndarray, ratio: float = UNCLIP_RATIO) -> np.ndarray | None:
    """Expand a shrunk DB box back to the true glyph extent."""
    area = cv2.contourArea(box.astype(np.float32))
    perimeter = cv2.arcLength(box.astype(np.float32), True)
    if perimeter <= 0:
        return None
    distance = area * ratio / perimeter

    offset = pyclipper.PyclipperOffset()
    offset.AddPath(box.astype(np.int64).tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    expanded = offset.Execute(distance)
    if not expanded:
        return None
    return np.array(expanded[0], dtype=np.float32)


def _order_clockwise(points: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    by_x = points[np.argsort(points[:, 0])]
    left, right = by_x[:2], by_x[2:]
    left = left[np.argsort(left[:, 1])]
    right = right[np.argsort(right[:, 1])]
    return np.array([left[0], right[0], right[1], left[1]], dtype=np.float32)


def boxes_from_bitmap(
    prob_map: np.ndarray,
    original_size: tuple[int, int],
    *,
    binary_threshold: float = BINARY_THRESHOLD,
    box_score_threshold: float = BOX_SCORE_THRESHOLD,
    unclip_ratio: float = UNCLIP_RATIO,
) -> list[np.ndarray]:
    """Turn a DB probability map into text quadrilaterals in original coordinates.

    *prob_map* is the (H, W) network output; *original_size* is the (height,
    width) of the image before detection resizing. Returns a list of (4, 2)
    float arrays ordered clockwise from the top-left corner.
    """
    if prob_map.ndim != 2:
        raise ValueError(f"expected a 2-D probability map, got shape {prob_map.shape}")

    map_height, map_width = prob_map.shape
    original_height, original_width = original_size
    bitmap = (prob_map > binary_threshold).astype(np.uint8)

    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    # The cap is a cost bound, so what it discards must be the least valuable
    # candidates. OpenCV returns contours in discovery order, which has nothing
    # to do with size — slicing that order drops real text lines on a dense
    # page while keeping specks. Largest first makes the cap lose the specks.
    if len(contours) > MAX_CANDIDATES:
        contours = sorted(contours, key=cv2.contourArea, reverse=True)

    boxes: list[np.ndarray] = []
    for contour in contours[:MAX_CANDIDATES]:
        if len(contour) < 4:
            continue
        rect = cv2.minAreaRect(contour)
        if min(rect[1]) < MIN_BOX_SIDE:
            continue
        box = cv2.boxPoints(rect)

        if _box_score(prob_map, box) < box_score_threshold:
            continue

        expanded = _unclip(box, unclip_ratio)
        if expanded is None or len(expanded) < 4:
            continue
        rect = cv2.minAreaRect(expanded)
        if min(rect[1]) < MIN_BOX_SIDE + 1:
            continue
        box = _order_clockwise(cv2.boxPoints(rect))

        box[:, 0] = np.clip(box[:, 0] / map_width * original_width, 0, original_width - 1)
        box[:, 1] = np.clip(box[:, 1] / map_height * original_height, 0, original_height - 1)
        boxes.append(box)

    return boxes


def sort_reading_order(boxes: list[np.ndarray], line_tolerance: float = 0.5) -> list[np.ndarray]:
    """Sort boxes top-to-bottom, then left-to-right within each visual line.

    Two boxes belong to the same line when their vertical centres are closer
    than *line_tolerance* times the smaller box height — this is what keeps a
    two-column heading from being read as two separate lines.
    """
    if not boxes:
        return []

    def centre_y(box: np.ndarray) -> float:
        return float(box[:, 1].mean())

    def height(box: np.ndarray) -> float:
        return float(box[:, 1].max() - box[:, 1].min())

    remaining = sorted(boxes, key=centre_y)
    ordered: list[np.ndarray] = []
    row: list[np.ndarray] = [remaining[0]]
    for box in remaining[1:]:
        reference = row[-1]
        limit = line_tolerance * min(height(reference), height(box))
        if abs(centre_y(box) - centre_y(reference)) <= limit:
            row.append(box)
        else:
            ordered.extend(sorted(row, key=lambda b: float(b[:, 0].min())))
            row = [box]
    ordered.extend(sorted(row, key=lambda b: float(b[:, 0].min())))
    return ordered
