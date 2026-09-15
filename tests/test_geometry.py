"""Tests for extractor/geometry.py — shared bounding-box utilities."""
import pytest

from extractor.geometry import rects_overlap


@pytest.mark.parametrize("a,b,expected", [
    # Overlapping cases
    ((0, 0, 10, 10), (5, 5, 15, 15), True),    # partial overlap
    ((0, 0, 10, 10), (0, 0, 10, 10), True),    # identical
    ((0, 0, 10, 10), (3, 3, 7, 7), True),      # b inside a
    ((3, 3, 7, 7), (0, 0, 10, 10), True),      # a inside b
    # Non-overlapping cases
    ((0, 0, 5, 5), (5, 0, 10, 5), False),      # touching at edge (not overlapping)
    ((0, 0, 5, 5), (6, 0, 10, 5), False),      # separated horizontally
    ((0, 0, 5, 5), (0, 6, 5, 10), False),      # separated vertically
    ((0, 0, 5, 5), (10, 10, 15, 15), False),   # far apart
], ids=[
    "partial-overlap",
    "identical",
    "b-inside-a",
    "a-inside-b",
    "touching-edge",
    "separated-horizontal",
    "separated-vertical",
    "far-apart",
])
def test_rects_overlap(a, b, expected):
    assert rects_overlap(a, b) is expected
