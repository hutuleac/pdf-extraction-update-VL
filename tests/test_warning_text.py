"""Plain-language rendering of warning dicts.

`describe()` is the last stop before a warning reaches a human — the
Markdown notes sidecar and the CLI run summary both call it after extraction
has already succeeded, so its own docstring guarantees it never raises on an
incomplete dict. That guarantee had no test: a KeyError here would masquerade
as an extraction failure pointing at the wrong file (see the comment at
warning_text.py:85-90). Every embellishment branch is exercised below, plus
the malformed-input paths the guarantee exists for.
"""
from extractor.warning_text import describe


def test_unknown_code_falls_back_to_the_code_itself():
    assert describe({"code": "SOME_FUTURE_CODE"}) == "SOME_FUTURE_CODE"


def test_page_number_is_prefixed():
    assert describe({"code": "GARBLED_TEXT", "page": 4}).startswith("Page 4: ")


def test_no_page_number_means_no_prefix():
    assert not describe({"code": "GARBLED_TEXT"}).startswith("Page")


def test_confidence_codes_append_a_percentage():
    text = describe({"code": "OCR_APPLIED", "confidence": 0.873})
    assert "(confidence 87%)" in text


def test_confidence_suppresses_the_raw_detail_suffix():
    """A confidence code's detail would repeat the percentage in worse words."""
    text = describe({
        "code": "OCR_APPLIED", "confidence": 0.9, "detail": "engine=fake",
    })
    assert "engine=fake" not in text


def test_non_confidence_code_appends_its_detail():
    text = describe({"code": "GARBLED_TEXT", "detail": "24 pages affected"})
    assert text.endswith("— 24 pages affected")


def test_ocr_mixed_confidence_reports_the_outlier_lines():
    text = describe({
        "code": "OCR_MIXED_CONFIDENCE", "low_lines": 3, "total_lines": 12, "lowest": 0.21,
    })
    assert "(3 of 12 lines, lowest 21%)" in text


def test_ocr_mixed_confidence_without_low_lines_key_is_not_embellished():
    """The docstring's guarantee: an incomplete warning drops the extra detail
    rather than raising — this is the branch that guarantee is about."""
    text = describe({"code": "OCR_MIXED_CONFIDENCE"})
    assert text == "some OCR lines on this page are much weaker than the page average"


def test_vlm_applied_notes_formulas_recovered():
    text = describe({"code": "VLM_APPLIED", "formulas": 5})
    assert "(5 formula(s) recovered)" in text


def test_vlm_applied_notes_redundant_prose():
    text = describe({"code": "VLM_APPLIED", "prose": "redundant"})
    assert "its prose repeated the page text and was dropped" in text


def test_vlm_applied_combines_both_notes():
    text = describe({"code": "VLM_APPLIED", "formulas": 2, "prose": "redundant"})
    assert "2 formula(s) recovered; its prose repeated the page text" in text


def test_vlm_applied_with_neither_note_is_unembellished():
    assert describe({"code": "VLM_APPLIED"}) == "the visual model read this page"


def test_vlm_figures_described_counts_pages():
    text = describe({"code": "VLM_FIGURES_DESCRIBED", "pages": 7})
    assert "(7 page(s))" in text


def test_vlm_describe_failed_counts_pages():
    text = describe({"code": "VLM_DESCRIBE_FAILED", "pages": 2})
    assert "(2 page(s))" in text


def test_vlm_describe_truncated_counts_pages():
    text = describe({"code": "VLM_DESCRIBE_TRUNCATED", "pages": 1})
    assert "(1 page(s))" in text


def test_vlm_output_rejected_with_page_count_names_the_duplicate_case():
    text = describe({"code": "VLM_OUTPUT_REJECTED", "pages": 4})
    assert text == (
        "4 page(s) the visual model read only repeated text the document "
        "already carries, and were discarded"
    )


def test_vlm_output_rejected_with_reason_instead_of_pages():
    text = describe({"code": "VLM_OUTPUT_REJECTED", "reason": "truncated"})
    assert text == "the visual model's reading of this page was discarded (truncated)"


def test_vlm_output_rejected_with_neither_field_is_unembellished():
    assert describe({"code": "VLM_OUTPUT_REJECTED"}) == (
        "the visual model's reading of this page was discarded"
    )


def test_formula_review_required_counts_formulas():
    text = describe({"code": "FORMULA_REVIEW_REQUIRED", "count": 3})
    assert "(3)" in text


def test_ocr_noise_filtered_quotes_the_sample():
    text = describe({
        "code": "OCR_NOISE_FILTERED", "dropped": 2, "sample": ["x", "##"],
    })
    assert "(2: 'x', '##')" in text


def test_ocr_noise_filtered_falls_back_to_sample_length_without_dropped_count():
    text = describe({"code": "OCR_NOISE_FILTERED", "sample": ["a", "b", "c"]})
    assert text.startswith("unreadable OCR fragments were discarded (3:")


# --- the docstring's guarantee: never raise, whatever the shape ------------

def test_completely_empty_warning_does_not_raise():
    assert describe({}) == ""


def test_missing_code_falls_back_to_empty_string_not_a_crash():
    assert describe({"page": 3}) == "Page 3: "
