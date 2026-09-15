"""Real OCR against the actual model files.

Skipped automatically when the OCR extra or the .onnx weights are absent, so
`pytest -m "not slow"` is the full logic surface on a machine without them.
"""
import pytest

from extractor.dispatcher import extract_document
from extractor.markdown_writer import write_markdown
from extractor.ocr import registry

pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(not registry.is_available(), reason="OCR engine or model not available"),
]

# The fixture writes these three lines as a picture; OCR has to read them back.
EXPECTED_PHRASES = ("Cerere de concediu", "Maria Popescu", "managerul de departament")
MIN_PHRASE_RETENTION = 0.90


def _ocr_text(model: dict) -> str:
    return "\n".join(
        block["content"]
        for unit in model["pages"]
        for block in unit["content"]
        if block.get("source") == "ocr"
    )


def _retention(text: str) -> float:
    found = sum(1 for phrase in EXPECTED_PHRASES if phrase in text)
    return found / len(EXPECTED_PHRASES)


def test_scanned_pdf_text_is_recovered(scanned_pdf):
    text = _ocr_text(extract_document(scanned_pdf))
    assert _retention(text) >= MIN_PHRASE_RETENTION, text


def test_ocr_block_records_its_provenance(scanned_pdf):
    blocks = [
        block for unit in extract_document(scanned_pdf)["pages"]
        for block in unit["content"] if block["type"] == "text"
    ]
    assert blocks, "expected OCR to produce a text block"
    assert blocks[0]["source"] == "ocr"
    assert 0.0 < blocks[0]["confidence"] <= 1.0


def test_applied_warning_names_the_engine_and_confidence(scanned_pdf):
    warnings = extract_document(scanned_pdf)["document"]["warnings"]
    applied = next(w for w in warnings if w["code"] == "OCR_APPLIED")
    assert applied["page"] == 1
    assert applied["engine"] == "onnx"
    assert applied["confidence"] > 0.6


def test_image_file_text_is_recovered(scanned_image):
    assert _retention(_ocr_text(extract_document(scanned_image))) >= MIN_PHRASE_RETENTION


def test_markdown_marks_ocr_text_as_recovered(scanned_pdf, tmp_path):
    text = write_markdown(extract_document(scanned_pdf), tmp_path).read_text(encoding="utf-8")
    assert "Text recovered by OCR (confidence" in text


def test_no_ocr_flag_really_stops_ocr(scanned_pdf):
    from extractor.ocr.config import configure

    configure(enabled=False)
    model = extract_document(scanned_pdf)
    assert _ocr_text(model) == ""
    assert any(w["code"] == "OCR_SKIPPED_DISABLED" for w in model["document"]["warnings"])


def test_layout_complex_page_picture_text_is_recovered(layout_complex_pdf):
    """The embedded picture on a layout-complex page must actually be read.

    Figure OCR is opt-in (``OcrConfig.figures`` defaults to False), so this
    must enable it explicitly or the picture is never sent to the engine.
    """
    from extractor.ocr.config import configure

    configure(figures=True)
    model = extract_document(layout_complex_pdf)
    assert model["pages"][0]["page_class"] == "layout-complex"
    assert _retention(_ocr_text(model)) >= MIN_PHRASE_RETENTION


def test_layout_complex_page_picture_skipped_without_ocr_figures_flag(layout_complex_pdf):
    """Without --ocr-figures, the embedded picture is never read (opt-in default)."""
    model = extract_document(layout_complex_pdf)
    assert model["pages"][0]["page_class"] == "layout-complex"
    assert _ocr_text(model) == ""


def test_layout_complex_page_native_text_survives_ocr(layout_complex_pdf):
    """OCR on the embedded picture must not disturb the page's own native text."""
    model = extract_document(layout_complex_pdf)
    native = " ".join(
        block["content"] for block in model["pages"][0]["content"]
        if block["type"] == "text" and block.get("source") != "ocr"
    )
    assert "Layout complex slide" in native
