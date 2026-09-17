"""Tests for extractor.rotated_text and its two wiring points.

Needs no model weights — PyMuPDF writes the fixtures at runtime.
"""
import pdfplumber
import pymupdf
import pytest

from extractor.pdf_reader import extract_pdf
from extractor.rotated_text import (
    drop_skewed_words,
    is_skewed,
    is_vertical,
    keep_char,
    repeated_vertical_boxes,
    repeated_vertical_words,
    skewed_words_and_text,
)

STAMP = "printed by A.User on 2026-09-03"
BODY = "The component shall be classified under one severity level."


def _char(matrix) -> dict:
    return {"object_type": "char", "matrix": matrix, "text": "x"}


# ---------------------------------------------------------------------------
# Direction classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dx,dy", [(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)])
def test_axis_aligned_directions_are_not_skewed(dx, dy):
    # Vertical text is legitimate content — sideways table headers, chart axis
    # labels, the author column on a title page.
    assert not is_skewed(dx, dy)


def test_diagonal_direction_is_skewed():
    assert is_skewed(0.62, -0.79)


def test_keep_char_drops_only_skewed_glyphs():
    assert keep_char(_char((1.0, 0.0, 0.0, 1.0)))       # upright
    assert keep_char(_char((0.0, 0.98, -0.98, 0.0)))    # 90 degrees
    assert not keep_char(_char((0.61, 0.79, -0.79, 0.61)))


def test_non_char_objects_always_survive():
    # The ruling lines a table is detected from must not be filtered away.
    assert keep_char({"object_type": "line"})
    assert keep_char({"object_type": "rect"})


def test_char_without_a_matrix_is_kept():
    assert keep_char({"object_type": "char"})


# ---------------------------------------------------------------------------
# Extraction from a real page
# ---------------------------------------------------------------------------


def _stamped_pdf(path, pages: int = 1, *, stamp: bool = True):
    """A PDF whose pages carry body text, optionally under a diagonal stamp."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 200), BODY, fontsize=11)
        if stamp:
            writer = pymupdf.TextWriter(page.rect)
            writer.append((72, 300), STAMP, fontsize=14)
            writer.write_text(page, morph=(pymupdf.Point(72, 300), pymupdf.Matrix(52)))
    doc.save(path)
    doc.close()
    return path


def test_stamp_is_found_and_body_text_is_not(tmp_path):
    path = _stamped_pdf(tmp_path / "stamped.pdf")
    with pymupdf.open(path) as doc:
        boxes, text = skewed_words_and_text(doc[0])

    assert text == STAMP
    # One box per word, rebuilt from the characters the same way PyMuPDF splits
    # words — that is what lets them be matched against get_text("words").
    assert len(boxes) == len(STAMP.split())


def test_clean_page_reports_nothing(tmp_path):
    path = _stamped_pdf(tmp_path / "clean.pdf", stamp=False)
    with pymupdf.open(path) as doc:
        boxes, text = skewed_words_and_text(doc[0])

    assert boxes == []
    assert text == ""


# ---------------------------------------------------------------------------
# End to end through the PDF reader
# ---------------------------------------------------------------------------


def _page_text(model) -> str:
    return " ".join(
        block["content"]
        for page in model["pages"]
        for block in page["content"]
        if block["type"] == "text"
    )


def test_stamp_is_absent_from_extracted_text(tmp_path):
    model = extract_pdf(_stamped_pdf(tmp_path / "stamped.pdf", pages=3))

    text = _page_text(model)
    assert BODY in text
    assert "printed by" not in text


def test_removal_is_reported(tmp_path):
    # Never silent: the angle test is reliable against a stamp but cannot know
    # that a one-off 45 degree chart label was content.
    model = extract_pdf(_stamped_pdf(tmp_path / "stamped.pdf", pages=3))

    warnings = [w for w in model["document"]["warnings"]
                if w["code"] == "ROTATED_TEXT_FILTERED"]
    assert len(warnings) == 1
    assert warnings[0]["pages"] == 3
    assert warnings[0]["sample"] == STAMP


def test_clean_document_gets_no_warning(tmp_path):
    model = extract_pdf(_stamped_pdf(tmp_path / "clean.pdf", pages=3, stamp=False))

    codes = [w["code"] for w in model["document"].get("warnings", [])]
    assert "ROTATED_TEXT_FILTERED" not in codes
    assert BODY in _page_text(model)


def test_body_words_the_stamp_crosses_survive(tmp_path):
    """A diagonal stamp is printed *over* the body, so its glyphs sit on real
    words. Excluding the stamp's area (rather than its words) deleted the prose
    underneath — "The component shall be" came back as "The be"."""
    path = _stamped_pdf(tmp_path / "overlap.pdf")
    with pymupdf.open(path) as doc:
        page = doc[0]
        boxes, _ = skewed_words_and_text(page)
        words = page.get_text("words")
        kept = drop_skewed_words(words, boxes)

    kept_text = " ".join(word[4] for word in kept)
    assert kept_text == BODY
    assert len(kept) == len(words) - len(STAMP.split())


# ---------------------------------------------------------------------------
# Vertical (axis-rotated) stamp detection — the watermark gap is_skewed
# deliberately leaves open, closed by repetition instead of angle.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("a,b,expected", [
    (0.0, 1.0, True), (0.0, -1.0, True),   # genuinely vertical (90/270 deg)
    (1.0, 0.0, False),                     # upright body text
    (0.62, -0.79, False),                  # diagonal — is_skewed's job, not this
])
def test_is_vertical_classifies_directions(a, b, expected):
    assert is_vertical(a, b) is expected


def _pdf_with_vertical_text(path, pages: int, texts: list[str]):
    """One page per entry in *texts*, each with that text written vertically
    (rotate=90) in the margin — texts repeated across entries model a stamp,
    a text appearing once models a genuine one-off rotated label."""
    doc = pymupdf.open()
    for text in texts[:pages]:
        page = doc.new_page()
        page.insert_text((72, 200), "Body text for this page.", fontsize=11)
        if text:
            page.insert_text((500, 200), text, fontsize=12, rotate=90)
    doc.save(path)
    doc.close()
    return path


def test_repeated_vertical_boxes_flags_a_margin_stamp(tmp_path):
    path = _pdf_with_vertical_text(tmp_path / "stamp.pdf", 6, ["MARGIN STAMP"] * 6)

    with pdfplumber.open(path) as pdf:
        boxes = repeated_vertical_boxes(pdf, min_pages=5)

    assert len(boxes) == 6
    assert all(boxes[n] for n in range(1, 7))


def test_repeated_vertical_boxes_keeps_a_one_off_label(tmp_path):
    # A sideways table header or chart label appears on one page — it must
    # never be treated as a stamp just because it is vertical.
    path = _pdf_with_vertical_text(
        tmp_path / "unique.pdf", 6, ["UNIQUE LABEL", "", "", "", "", ""],
    )

    with pdfplumber.open(path) as pdf:
        boxes = repeated_vertical_boxes(pdf, min_pages=5)

    assert boxes == {}


def test_repeated_vertical_words_flags_a_repeating_tab(tmp_path):
    path = _pdf_with_vertical_text(tmp_path / "tab.pdf", 6, ["SECTION TAB"] * 6)
    with pymupdf.open(path) as doc:
        stamped = repeated_vertical_words(doc, min_pages=5)
    assert sorted(stamped) == [1, 2, 3, 4, 5, 6]
    assert [t for _, t in stamped[1]] == ["SECTION", "TAB"]


def test_repeated_vertical_words_keeps_one_off_labels(tmp_path):
    labels = [f"http://example.org/{n}" for n in range(6)]
    path = _pdf_with_vertical_text(tmp_path / "credits.pdf", 6, labels)
    with pymupdf.open(path) as doc:
        assert repeated_vertical_words(doc, min_pages=5) == {}


def test_vertical_stamp_is_absent_from_extracted_text(tmp_path):
    # The wiring point: the text pass drops it, not only the table pass.
    path = _pdf_with_vertical_text(tmp_path / "tab.pdf", 6, ["SECTION TAB"] * 6)
    text = _page_text(extract_pdf(path))
    assert "Body text" in text
    assert "SECTION" not in text


def test_repeated_vertical_boxes_ignores_short_documents(tmp_path):
    # min_pages is a floor: a stamp on every page of a 3-page document is not
    # yet evidence of document furniture (mirrors repeated_image_xrefs).
    path = _pdf_with_vertical_text(tmp_path / "short.pdf", 3, ["STAMP"] * 3)

    with pdfplumber.open(path) as pdf:
        boxes = repeated_vertical_boxes(pdf, min_pages=5)

    assert boxes == {}
