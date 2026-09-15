"""Tests for the recognition-input preparation in ``extractor.ocr.onnx_engine``.

No .onnx weights are needed: a stub recognition session stands in for the real
one, so these run wherever cv2 and numpy are importable.
"""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from extractor.ocr import onnx_engine  # noqa: E402
from extractor.ocr.onnx_engine import (  # noqa: E402
    REC_HEIGHT,
    REC_MAX_WIDTH,
    OnnxOcrEngine,
    _recognition_batch,
    _target_width,
    _width_ordered_batches,
)

LABELS = ["<blank>", "a", "b", "c", " "]


def _crop(width: int, height: int, fill: int = 200) -> np.ndarray:
    return np.full((height, width, 3), fill, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Width budget
# ---------------------------------------------------------------------------

def test_a_long_line_of_small_text_is_not_crushed_horizontally():
    """A wide crop keeps its aspect ratio; squeezing it merges characters.

    A 2400x60 line asks for 1920px at the 48px recognition height. Capping that
    at 640 compressed it 3x, which merged glyphs and silently lost the spaces
    between words.
    """
    assert _target_width(_crop(2400, 60)) == 1920


def test_aspect_ratio_is_preserved_for_ordinary_lines():
    assert _target_width(_crop(300, 48)) == 300
    assert _target_width(_crop(600, 96)) == 300


def test_an_absurdly_wide_crop_is_still_bounded():
    """The cap must still exist — it just has to sit above real text lines."""
    assert _target_width(_crop(100_000, 48)) == REC_MAX_WIDTH


def test_batch_tensor_uses_the_uncrushed_width():
    batch = _recognition_batch([_crop(2400, 60)])
    assert batch.shape == (1, 3, REC_HEIGHT, 1920)


# ---------------------------------------------------------------------------
# Width-grouped batching
# ---------------------------------------------------------------------------

def test_crops_are_batched_with_others_of_similar_width():
    """Every batch pads to its widest member, so mixing widths wastes work."""
    crops = [_crop(1600, 48), _crop(100, 48), _crop(1600, 48), _crop(100, 48)]

    batches = _width_ordered_batches(crops, size=2)

    assert sorted(tuple(sorted(b)) for b in batches) == [(0, 2), (1, 3)]


def test_batching_covers_every_crop_exactly_once():
    crops = [_crop(w, 48) for w in (500, 100, 900, 300, 700)]

    seen = [index for batch in _width_ordered_batches(crops, size=2) for index in batch]

    assert sorted(seen) == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Reading order survives the reordering
# ---------------------------------------------------------------------------

class StubSession:
    """Returns one-hot logits that spell the character each crop was filled with."""

    def run(self, _outputs, feed):
        batch = next(iter(feed.values()))
        rows = []
        for item in batch:
            fill = round((item[0, 0, 0] * 0.5 + 0.5) * 255)
            row = np.zeros((1, len(LABELS)), dtype=np.float32)
            row[0, fill] = 1.0
            rows.append(row)
        return [np.stack(rows)]


def _box(x0: float, x1: float) -> np.ndarray:
    return np.array([[x0, 0], [x1, 0], [x1, 10], [x0, 10]], dtype=np.float32)


def test_lines_come_back_in_box_order_not_in_width_order(monkeypatch):
    """Batching by width must not leak into the order the reader sees."""
    monkeypatch.setattr(onnx_engine, "REC_BATCH", 2)
    engine = object.__new__(OnnxOcrEngine)
    engine._rec = StubSession()
    engine._rec_input = "x"
    engine._labels = LABELS

    # Widths descend while reading order ascends, so width-sorting reverses them.
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    crops = [_crop(900, 48, fill=1), _crop(500, 48, fill=2), _crop(100, 48, fill=3)]
    monkeypatch.setattr(onnx_engine, "_crop_box", lambda _img, box: crops[int(box[0][0])])
    boxes = [_box(0, 1), _box(1, 2), _box(2, 3)]

    lines = engine._read(image, boxes)

    assert [line.text for line in lines] == ["a", "b", "c"]
