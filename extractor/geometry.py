"""Shared geometric utilities for bounding-box operations."""


def rects_overlap(a: tuple[float, float, float, float],
                  b: tuple[float, float, float, float]) -> bool:
    """True if rectangles a and b (x0, y0, x1, y1) overlap in area."""
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])
