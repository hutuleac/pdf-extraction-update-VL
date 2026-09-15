"""The visual layer's blocks reaching JSON and Markdown, and the merge rules."""
import pymupdf
import pytest

from extractor.markdown_writer import write_markdown
from extractor.model import make_vlm_text_block
from extractor.pdf_reader import extract_pdf
from extractor.vlm import config
from extractor.vlm.doctag import ParsedPage


@pytest.fixture(autouse=True)
def _clean():
    config.reset()
    yield
    config.reset()


@pytest.fixture
def one_page_pdf(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "native text")
    out = tmp_path / "doc.pdf"
    doc.save(out)
    doc.close()
    return out


def _stub_vlm(monkeypatch, parsed, *, page_class="native-text"):
    """Feed one parsed page in, and force the page's class."""
    from extractor import pdf_reader

    monkeypatch.setattr(
        pdf_reader, "vlm_pages",
        lambda *a, **k: ({1: parsed}, [{"code": "VLM_APPLIED", "page": 1, "formulas": 0}]),
    )
    monkeypatch.setattr(pdf_reader, "classify_page", lambda signals: page_class)


def test_make_vlm_text_block_marks_its_source():
    assert make_vlm_text_block("Recovered prose") == {
        "type": "text", "content": "Recovered prose", "source": "vlm",
    }


def test_make_vlm_text_block_is_none_when_empty():
    assert make_vlm_text_block("   ") is None


def test_disabled_leaves_output_unchanged(one_page_pdf):
    """The default path must be identical to a run without the layer."""
    doc = extract_pdf(one_page_pdf)

    assert not [b for b in doc["pages"][0]["content"] if b.get("source") == "vlm"]
    assert not [
        w for w in doc["document"].get("warnings", []) if w["code"].startswith("VLM")
    ]


def test_vlm_text_is_added_beside_healthy_native_text(one_page_pdf, monkeypatch):
    _stub_vlm(monkeypatch, ParsedPage(text="Recovered prose"))

    blocks = extract_pdf(one_page_pdf)["pages"][0]["content"]
    texts = [b for b in blocks if b["type"] == "text"]

    assert [b.get("source") for b in texts] == [None, "vlm"]
    assert "native text" in texts[0]["content"]


def test_vlm_text_replaces_untrusted_native_text(one_page_pdf, monkeypatch):
    """Same precedence OCR already had on a garbled page: the native text is
    known wrong, so publishing both would publish one of them as prose."""
    _stub_vlm(monkeypatch, ParsedPage(text="Recovered prose"), page_class="garbled")

    texts = [b for b in extract_pdf(one_page_pdf)["pages"][0]["content"] if b["type"] == "text"]

    assert [b.get("source") for b in texts] == ["vlm"]


def test_vlm_tables_are_taken_only_when_the_page_has_none(one_page_pdf, monkeypatch):
    _stub_vlm(monkeypatch, ParsedPage(text="prose", tables=[[["a", "b"], ["1", "2"]]]))

    tables = [b for b in extract_pdf(one_page_pdf)["pages"][0]["content"] if b["type"] == "table"]

    assert [t["content"] for t in tables] == [[["a", "b"], ["1", "2"]]]


def test_native_tables_win_over_the_models(one_page_pdf, monkeypatch):
    from extractor import pdf_reader

    _stub_vlm(monkeypatch, ParsedPage(text="prose", tables=[[["model", "guess"]]]))
    monkeypatch.setattr(
        pdf_reader, "extract_tables",
        lambda path: {1: [{"cells": [["real", "cell"]], "bbox": (0, 0, 1, 1), "origin": (0, 0)}]},
    )

    tables = [b for b in extract_pdf(one_page_pdf)["pages"][0]["content"] if b["type"] == "table"]

    assert [t["content"] for t in tables] == [[["real", "cell"]]]


def test_a_reading_without_text_still_leaves_the_page_to_ocr(one_page_pdf, monkeypatch):
    """A garbled page accepted for its tables alone replaces no text. Skipping
    OCR there would leave the garbled characters standing with nothing tried."""
    from extractor import pdf_reader

    _stub_vlm(monkeypatch, ParsedPage(text="", tables=[[["a"]]]), page_class="garbled")
    seen = {}
    monkeypatch.setattr(
        pdf_reader, "_ocr_pages",
        lambda path, classes, skip=None, page_image_counts=None: (seen.setdefault("skip", skip) and {}) or {},
    )

    extract_pdf(one_page_pdf)

    assert seen["skip"] == set()


def test_markdown_flags_visually_read_text(one_page_pdf, monkeypatch, tmp_path):
    _stub_vlm(monkeypatch, ParsedPage(text="Recovered prose"))

    out = write_markdown(extract_pdf(one_page_pdf), tmp_path)
    text = out.read_text(encoding="utf-8")

    assert "> Read from the page image by the visual model" in text
    assert "Recovered prose" in text
