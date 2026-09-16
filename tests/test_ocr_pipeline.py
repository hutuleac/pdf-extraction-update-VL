"""The behaviour that must hold on a machine with no OCR at all.

These tests never touch a model: the registry is forced into its unavailable
state, which is exactly the situation the repository has to survive cleanly.
"""
import json

import pytest

from extractor.dispatcher import extract_document
from extractor.json_writer import write_json
from extractor.markdown_writer import write_markdown
from extractor.ocr import registry
from extractor.ocr.base import OcrUnavailable, UnavailableReason
from main import file_stats, format_summary


@pytest.fixture
def no_ocr(monkeypatch):
    """Force the registry to report OCR as unavailable, with a real reason."""
    failure = OcrUnavailable(UnavailableReason.MISSING_MODEL_DIR, "looked in: nowhere")
    monkeypatch.setattr(registry, "is_available", lambda: False)
    monkeypatch.setattr(registry, "unavailable_reason", lambda: failure)
    return failure


def _codes(model: dict) -> list[str]:
    return [w["code"] for w in model["document"].get("warnings", [])]


# ---------------------------------------------------------------------------
# The fixture must really be a scan before OCR is asked to read it
# ---------------------------------------------------------------------------

def test_scanned_fixture_is_classified_as_scanned(scanned_pdf, no_ocr):
    model = extract_document(scanned_pdf)
    assert model["pages"][0]["page_class"] == "scanned"


# ---------------------------------------------------------------------------
# Scanned PDF without OCR
# ---------------------------------------------------------------------------

def test_scanned_page_without_ocr_says_why(scanned_pdf, no_ocr):
    model = extract_document(scanned_pdf)
    assert "OCR_UNAVAILABLE" in _codes(model)
    warning = next(w for w in model["document"]["warnings"] if w["code"] == "OCR_UNAVAILABLE")
    assert warning["page"] == 1
    assert "model directory" in warning["detail"]


def test_scanned_page_without_ocr_produces_no_text_blocks(scanned_pdf, no_ocr):
    model = extract_document(scanned_pdf)
    assert [b for b in model["pages"][0]["content"] if b["type"] == "text"] == []


def test_no_ocr_flag_reports_itself_as_the_reason(scanned_pdf):
    from extractor.ocr.config import configure

    configure(enabled=False)
    model = extract_document(scanned_pdf)
    assert "OCR_SKIPPED_DISABLED" in _codes(model)


# ---------------------------------------------------------------------------
# layout-complex pages keep their native text and are never OCR'd
# ---------------------------------------------------------------------------

def test_layout_complex_fixture_is_classified_as_layout_complex(layout_complex_pdf, no_ocr):
    model = extract_document(layout_complex_pdf)
    assert model["pages"][0]["page_class"] == "layout-complex"


def test_layout_complex_page_with_image_is_not_ocrd(layout_complex_pdf, no_ocr):
    """The picture is exported and left to the VLM layer; OCR'ing it returned
    axis labels and legend fragments, most of them duplicating the page text
    the figure was rendered over."""
    model = extract_document(layout_complex_pdf)
    assert "OCR_UNAVAILABLE" not in _codes(model)


def test_layout_complex_page_without_image_skips_ocr(layout_complex_pdf_no_image, no_ocr):
    model = extract_document(layout_complex_pdf_no_image)
    assert model["pages"][0]["page_class"] == "layout-complex"
    assert "OCR_UNAVAILABLE" not in _codes(model)


def test_ocr_figures_flag_sends_the_picture_to_ocr(layout_complex_pdf, no_ocr):
    """--ocr-figures restores the pre-1.5 path for decks whose figures carry
    the only text on the page."""
    from extractor.ocr.config import configure

    configure(figures=True)
    model = extract_document(layout_complex_pdf)
    assert "OCR_UNAVAILABLE" in _codes(model)


def test_ocr_figures_flag_still_skips_a_page_with_no_picture(
    layout_complex_pdf_no_image, no_ocr,
):
    """Nothing to read means nothing to rasterize, flag or no flag."""
    from extractor.ocr.config import configure

    configure(figures=True)
    model = extract_document(layout_complex_pdf_no_image)
    assert "OCR_UNAVAILABLE" not in _codes(model)


def test_layout_complex_page_keeps_its_native_text(layout_complex_pdf, no_ocr):
    """Embedded-image OCR must never touch the page's own native text."""
    model = extract_document(layout_complex_pdf)
    text = " ".join(
        b["content"] for b in model["pages"][0]["content"] if b["type"] == "text"
    )
    assert "Layout complex slide" in text


# ---------------------------------------------------------------------------
# Image files
# ---------------------------------------------------------------------------

def test_image_file_is_a_supported_document(scanned_image, no_ocr):
    model = extract_document(scanned_image)
    assert model["document"]["source_type"] == "image"
    assert model["document"]["pages"] == 1


def test_image_file_without_ocr_still_writes_both_outputs(scanned_image, no_ocr, tmp_path):
    model = extract_document(scanned_image)
    json_path = write_json(model, tmp_path / "json")
    md_path = write_markdown(model, tmp_path / "md")

    assert json.loads(json_path.read_text(encoding="utf-8"))["document"]["source_type"] == "image"
    assert "Extraction Notes" in md_path.read_text(encoding="utf-8")


def test_unreadable_image_is_a_warning_not_a_crash(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"this is not a PNG")
    model = extract_document(broken)
    assert "OCR_FAILED" in _codes(model)


# ---------------------------------------------------------------------------
# Surfacing: Markdown and CLI
# ---------------------------------------------------------------------------

def test_markdown_notes_explain_the_gap_in_plain_language(scanned_pdf, no_ocr, tmp_path):
    text = write_markdown(extract_document(scanned_pdf), tmp_path).read_text(encoding="utf-8")
    assert "## Extraction Notes" in text
    assert "Page 1: no text extracted — no OCR model directory was found" in text


def test_clean_documents_get_no_extraction_notes(sample_pdf, no_ocr, tmp_path):
    text = write_markdown(extract_document(sample_pdf), tmp_path).read_text(encoding="utf-8")
    assert "Extraction Notes" not in text


def test_cli_summary_counts_unreadable_pages_and_names_the_fix(scanned_pdf, no_ocr):
    stats = file_stats(extract_document(scanned_pdf))
    assert stats["unreadable_pages"] == 1
    assert stats["ocr_pages"] == 0

    summary = "\n".join(format_summary([stats]))
    assert "1 page(s) could not be extracted" in summary
    assert 'pip install -e ".[ocr]"' in summary


def test_cli_summary_stays_quiet_for_clean_documents(sample_pdf, no_ocr):
    summary = "\n".join(format_summary([file_stats(extract_document(sample_pdf))]))
    assert "could not be extracted" not in summary


def test_cli_summary_names_vlm_unavailable_and_the_fix():
    stats = {
        "ok": True, "filename": "doc.pdf", "source_type": "pdf",
        "units": 1, "text_blocks": 1, "table_blocks": 0, "image_count": 0,
        "warnings": [{
            "code": "VLM_UNAVAILABLE",
            "detail": "the visual model could not be loaded (missing torchvision)",
            "pages": 1,
        }],
    }
    summary = "\n".join(format_summary([stats]))
    assert "--vlm was requested but the visual model is unavailable" in summary
    assert "missing torchvision" in summary
    assert 'pip install -e ".[vlm]"' in summary


# ---------------------------------------------------------------------------
# Nothing changes for documents that never needed OCR
# ---------------------------------------------------------------------------

def test_text_pdf_is_untouched_by_the_ocr_path(sample_pdf, no_ocr):
    model = extract_document(sample_pdf)
    assert not any(code.startswith("OCR_") for code in _codes(model))
    assert all("source" not in b for unit in model["pages"] for b in unit["content"])
