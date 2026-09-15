"""Routing, rejection and caching for the visual layer, with a stub engine."""
import pymupdf
import pytest

from extractor.vlm import apply, config, registry


@pytest.fixture(autouse=True)
def _clean():
    config.reset()
    yield
    config.reset()


@pytest.fixture
def two_page_pdf(tmp_path):
    doc = pymupdf.open()
    # The two pages must differ: the inference cache is keyed on rendered
    # pixels, so identical pages would share one entry and the second page
    # would never reach the engine.
    for number in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"native text on page {number}")
    out = tmp_path / "doc.pdf"
    doc.save(out)
    doc.close()
    return out


class StubEngine:
    """Returns a canned doctag per call and records what it was asked to do."""

    def __init__(self, outputs):
        self.name = "stub"
        self.outputs = list(outputs)
        self.calls = 0

    def convert(self, png_bytes, *, max_tokens):
        self.calls += 1
        return self.outputs.pop(0)


def _install(monkeypatch, engine, tmp_path):
    config.configure(enabled=True, cache_dir=str(tmp_path / "cache"))
    monkeypatch.setattr(registry, "is_available", lambda: True)
    monkeypatch.setattr(registry, "get_engine", lambda: engine)


GOOD = ("<doctag><text><loc_1><loc_1><loc_1><loc_1>"
        "Recovered prose that is comfortably longer than the native text."
        "</text><formula>a = \\frac { b } { c }</formula></doctag>")

TRUNCATED = "<doctag><formula>\\left[ \\frac{z}{b} \\left[ \\frac{z}{b}"

EMPTY = "<doctag></doctag>"

# One character of native text per repeat, sharing no words with GOOD, so the
# yield floor sees the length it is given and the novelty check stays clear.
NATIVE = "z"


# --- routing ---------------------------------------------------------------

def test_every_page_is_read(two_page_pdf, monkeypatch, tmp_path):
    """Whole-document scope: the model sees healthy pages too, because the
    formulas and table structure it recovers are invisible to the native path."""
    engine = StubEngine([GOOD, GOOD])
    _install(monkeypatch, engine, tmp_path)

    pages, _ = apply.vlm_pages(two_page_pdf, ["native-text", "garbled"], [NATIVE * 900, NATIVE * 10])

    assert engine.calls == 2
    assert set(pages) == {1, 2}


def test_disabled_reads_nothing(two_page_pdf):
    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "garbled"], [NATIVE * 10, NATIVE * 10])

    assert pages == {}
    assert warnings == []


# --- unavailability --------------------------------------------------------

def test_unavailable_warns_once_and_does_not_crash(two_page_pdf, monkeypatch):
    config.configure(enabled=True)
    monkeypatch.setattr(registry, "is_available", lambda: False)
    monkeypatch.setattr(registry, "unavailable_reason", lambda: None)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "garbled"], [NATIVE * 10, NATIVE * 10])

    assert pages == {}
    assert [w["code"] for w in warnings] == ["VLM_UNAVAILABLE"]


# --- acceptance and rejection ---------------------------------------------

def test_accepted_page_reports_vlm_applied(two_page_pdf, monkeypatch, tmp_path):
    _install(monkeypatch, StubEngine([GOOD, EMPTY]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 10, NATIVE * 900])

    assert pages[1].formula_count == 1
    applied = [w for w in warnings if w["code"] == "VLM_APPLIED"]
    assert applied == [{"code": "VLM_APPLIED", "page": 1, "formulas": 1}]


def test_truncated_output_is_rejected(two_page_pdf, monkeypatch, tmp_path):
    _install(monkeypatch, StubEngine([TRUNCATED, EMPTY]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 10, NATIVE * 900])

    assert pages == {}
    rejected = [w for w in warnings if w["code"] == "VLM_OUTPUT_REJECTED"]
    assert rejected[0]["page"] == 1
    assert rejected[0]["reason"] == "truncated"


def test_empty_output_is_rejected(two_page_pdf, monkeypatch, tmp_path):
    _install(monkeypatch, StubEngine([EMPTY, EMPTY]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 900, NATIVE * 900])

    assert pages == {}
    assert {w["reason"] for w in warnings if w["code"] == "VLM_OUTPUT_REJECTED"} == {"empty"}


def test_prose_that_repeats_the_page_text_is_dropped(two_page_pdf, monkeypatch, tmp_path):
    """The measured failure on two reference PDFs: on a healthy page the model
    returned the native text back, 92-100% word for word. Printed beside it
    that doubles the document; only the equations are new."""
    native = "Recovered prose that is comfortably longer than the native text."
    _install(monkeypatch, StubEngine([GOOD, GOOD]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["native-text", "garbled"], [native, native])

    assert pages[1].text == "$$\na = \\frac { b } { c }\n$$"
    assert [w for w in warnings if w["page"] == 1][0]["prose"] == "redundant"
    # The garbled page is replacing the text, so its prose is what is wanted.
    assert "Recovered prose" in pages[2].text


def test_duplicate_prose_with_nothing_else_is_rejected(two_page_pdf, monkeypatch, tmp_path):
    plain = ("<doctag><text><loc_1><loc_1><loc_1><loc_1>"
             "Recovered prose that is comfortably longer than the native text."
             "</text></doctag>")
    native = "Recovered prose that is comfortably longer than the native text."
    _install(monkeypatch, StubEngine([plain, plain]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["native-text", "native-text"], [native, native])

    assert pages == {}
    # Counted once for the document, not listed page by page: on a text
    # document this is the common case and a line each buries the real faults.
    assert warnings == [{"code": "VLM_OUTPUT_REJECTED", "reason": "duplicate", "pages": 2}]


def test_thin_output_is_rejected_only_where_it_would_replace(
    two_page_pdf, monkeypatch, tmp_path,
):
    """The yield floor exists to stop a bad reading overwriting a garbled
    page's text. On a healthy page the output is additive, so a partial
    reading is still a gain — rejecting it would throw away the one formula
    the native path could never see."""
    _install(monkeypatch, StubEngine([GOOD, GOOD]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "layout-complex"], [NATIVE * 9000, NATIVE * 9000])

    assert set(pages) == {2}
    rejected = [w for w in warnings if w["code"] == "VLM_OUTPUT_REJECTED"]
    assert rejected == [{"code": "VLM_OUTPUT_REJECTED", "page": 1, "reason": "low-yield"}]


def test_rejected_formulas_raise_a_review_warning(two_page_pdf, monkeypatch, tmp_path):
    raw = ("<doctag><text><loc_1><loc_1><loc_1><loc_1>"
           "Prose long enough to clear the yield floor comfortably.</text>"
           "<formula>\\left[ \\frac{z}{b}</formula></doctag>")
    _install(monkeypatch, StubEngine([raw, EMPTY]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 10, NATIVE * 900])

    assert set(pages) == {1}
    review = [w for w in warnings if w["code"] == "FORMULA_REVIEW_REQUIRED"]
    assert review == [{"code": "FORMULA_REVIEW_REQUIRED", "page": 1, "count": 1}]


def test_one_page_failing_does_not_lose_the_others(two_page_pdf, monkeypatch, tmp_path):
    class Exploding(StubEngine):
        def convert(self, png_bytes, *, max_tokens):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("out of memory")
            return GOOD

    _install(monkeypatch, Exploding([]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "garbled"], [NATIVE * 10, NATIVE * 10])

    assert set(pages) == {2}
    assert any(w["code"] == "VLM_OUTPUT_REJECTED" and w["page"] == 1 for w in warnings)


# --- caching ---------------------------------------------------------------

def test_second_run_reuses_the_cache(two_page_pdf, monkeypatch, tmp_path):
    engine = StubEngine([GOOD, GOOD, GOOD, GOOD])
    _install(monkeypatch, engine, tmp_path)

    first, _ = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 10, NATIVE * 900])
    second, _ = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 10, NATIVE * 900])

    assert engine.calls == 2, "the second run must not re-infer"
    assert first[1].text == second[1].text


def test_changing_max_tokens_invalidates_the_cache(two_page_pdf, monkeypatch, tmp_path):
    engine = StubEngine([GOOD] * 4)
    _install(monkeypatch, engine, tmp_path)

    apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 10, NATIVE * 900])
    config.configure(max_tokens=99)
    apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [NATIVE * 10, NATIVE * 900])

    assert engine.calls == 4
