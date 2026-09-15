import pymupdf

from extractor.pdf_reader import extract_pdf, read_document


def _pdf_with_image(path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A page with one picture.")
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200))
    pixmap.clear_with(180)
    page.insert_image(pymupdf.Rect(72, 100, 272, 300), pixmap=pixmap)
    doc.save(path)
    doc.close()
    return path


def test_extract_pdf_saves_images_when_images_dir_given(tmp_path):
    pdf_path = _pdf_with_image(tmp_path / "with_image.pdf")
    images_dir = tmp_path / "with_image_images"

    model = extract_pdf(pdf_path, images_dir=images_dir)

    image_blocks = [b for b in model["pages"][0]["content"] if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert (images_dir / image_blocks[0]["path"].split("/")[-1]).exists()


def test_extract_pdf_without_images_dir_only_counts_images(tmp_path):
    pdf_path = _pdf_with_image(tmp_path / "with_image.pdf")

    model = extract_pdf(pdf_path)

    assert not any(b["type"] == "image" for b in model["pages"][0]["content"])
    assert model["pages"][0]["image_count"] == 1


def test_extract_pdf_model_shape(table_pdf):
    model = extract_pdf(table_pdf)
    assert model["document"]["source_type"] == "pdf"
    assert model["document"]["filename"] == "table_test.pdf"
    assert model["document"]["pages"] == 1
    unit = model["pages"][0]
    assert unit["unit"] == 1
    assert unit["unit_type"] == "page"
    types = [b["type"] for b in unit["content"]]
    assert "text" in types
    assert "table" in types


def test_extract_pdf_dedup(table_pdf):
    model = extract_pdf(table_pdf)
    blocks = model["pages"][0]["content"]
    text = " ".join(b["content"] for b in blocks if b["type"] == "text")
    assert "Inregistreaza" not in text


def test_two_column_warning_only_for_concurrent_columns(tmp_path):
    """A page split into blocks that don't run side-by-side (one entirely
    above the other) reads correctly top-to-bottom regardless of column
    order, so it should not be flagged as an ambiguous two-column page."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Top block spanning the page.")
    page.insert_text((300, 400), "Bottom block, unrelated column.")
    path = tmp_path / "stacked.pdf"
    doc.save(path)
    doc.close()

    model = extract_pdf(path)

    codes = [w["code"] for w in model["document"].get("warnings", [])]
    assert "POSSIBLE_TWO_COLUMN_ORDER" not in codes


def test_two_column_warning_fires_for_concurrent_columns(tmp_path):
    """Two blocks running side-by-side down the page are genuinely
    ambiguous to read in order, so the warning should still fire."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Left column line one.")
    page.insert_text((50, 120), "Left column line two.")
    page.insert_text((300, 100), "Right column line one.")
    page.insert_text((300, 120), "Right column line two.")
    path = tmp_path / "side_by_side.pdf"
    doc.save(path)
    doc.close()

    model = extract_pdf(path)

    codes = [w["code"] for w in model["document"].get("warnings", [])]
    assert "POSSIBLE_TWO_COLUMN_ORDER" in codes


def test_read_document_basic(sample_pdf):
    data = read_document(sample_pdf)
    assert data["filename"] == "procedura_test.pdf"
    assert data["pages"] == 2
    assert len(data["page_texts"]) == 2
    assert "manager" in data["page_texts"][0].lower()
    assert data["page_image_counts"] == [0, 0]
    assert isinstance(data["author"], str)
    assert isinstance(data["title"], str)


# ---------------------------------------------------------------------------
# Garbled pages: OCR that is not published must not be reported as recovered
# ---------------------------------------------------------------------------

def _garbled_reader_data(text: str) -> dict:
    """One-page reader_data classified 'garbled', so extract_pdf takes that path."""
    return {
        "filename": "garbled.pdf", "pages": 1, "author": "", "title": "",
        "page_texts": [text], "page_image_counts": [0], "page_classes": ["garbled"],
        "page_warnings": [[]], "page_heights": [792.0], "page_widths": [612.0],
        "page_words": [[]], "page_column_counts": [1],
        "page_concurrent_columns": [False], "page_rotated_text": [""],
    }


def _stub_garbled_page(monkeypatch, confidence: float) -> None:
    """Drive extract_pdf with a garbled page whose OCR scored *confidence*."""
    from extractor import pdf_reader
    from extractor.model import make_ocr_text_block

    ocr_block = make_ocr_text_block("clean recovered sentence", confidence)
    applied = {"code": "OCR_APPLIED", "page": 1, "confidence": confidence}
    monkeypatch.setattr(pdf_reader, "extract_tables", lambda path: {})
    monkeypatch.setattr(
        pdf_reader, "read_document",
        lambda path, exclude_regions=None: _garbled_reader_data("gaibled nalive lexl"),
    )
    monkeypatch.setattr(
        pdf_reader, "_ocr_pages",
        lambda path, classes, skip=None, page_image_counts=None: {1: (ocr_block, [applied])},
    )
    monkeypatch.setattr(pdf_reader, "_min_confidence", lambda: 0.60)


def test_rejected_ocr_does_not_claim_text_was_recovered(tmp_path, monkeypatch):
    """Saying 'recovered by OCR' about text the document does not hold is worse
    than saying nothing — it stops the reader going back to the source."""
    _stub_garbled_page(monkeypatch, confidence=0.31)

    model = extract_pdf(tmp_path / "garbled.pdf")

    codes = [w["code"] for w in model["document"]["warnings"]]
    assert "OCR_APPLIED" not in codes
    assert "OCR_REJECTED_LOW_CONFIDENCE" in codes


def test_rejected_ocr_text_is_absent_from_the_document(tmp_path, monkeypatch):
    _stub_garbled_page(monkeypatch, confidence=0.31)

    model = extract_pdf(tmp_path / "garbled.pdf")

    text = " ".join(b["content"] for b in model["pages"][0]["content"])
    assert "clean recovered sentence" not in text


def test_accepted_ocr_still_reports_applied(tmp_path, monkeypatch):
    _stub_garbled_page(monkeypatch, confidence=0.92)

    model = extract_pdf(tmp_path / "garbled.pdf")

    codes = [w["code"] for w in model["document"]["warnings"]]
    assert "OCR_APPLIED" in codes
    assert "OCR_REJECTED_LOW_CONFIDENCE" not in codes
    text = " ".join(b["content"] for b in model["pages"][0]["content"])
    assert "clean recovered sentence" in text
