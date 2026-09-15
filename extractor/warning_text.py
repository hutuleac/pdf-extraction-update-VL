"""Plain-language wording for warning codes.

Warnings used to reach only the JSON, where a code like ``SCANNED_PAGE_NO_TEXT``
means nothing to a reader. The Markdown writer and the CLI summary both need
the same sentence, so it is written once here.
"""

_TEMPLATES = {
    # Stays factual about the file itself: OCR may still have recovered the text,
    # and the OCR_APPLIED note sits right beside this one when it did.
    "SCANNED_PAGE_NO_TEXT": "scanned page — the file itself carries no text layer",
    "MIXED_CONTENT_PAGE": "page mixes text and large images",
    "GARBLED_TEXT": "text on this page is damaged or unreadable",
    "LAYOUT_COMPLEX": "complex layout — reading order may be imperfect",
    "HEADER_FOOTER_DETECTED": "repeated headers or footers were removed from the text",
    "POSSIBLE_TWO_COLUMN_ORDER": "two-column layout — reading order may be imperfect",
    "UNICODE_REPAIRED": "damaged characters were repaired",
    "ENCODING_FALLBACK": "the file encoding had to be guessed",
    # Truncating beats the RecursionError this used to raise, which failed the
    # whole file and lost its shallow content along with the deep part.
    "NESTING_TRUNCATED": "the document nests deeper than the reader renders — the deepest levels were cut",
    # These three always arrive with a detail that names the exact cause, so the
    # template stays short instead of repeating it.
    "OCR_UNAVAILABLE": "no text extracted",
    "OCR_SKIPPED_DISABLED": "no text extracted",
    "OCR_MODEL_INCOMPATIBLE": "no text extracted",
    "OCR_FAILED": "no text extracted — OCR could not read this page",
    "OCR_IMAGE_FAILED": "some images on this page could not be read — text from the rest was kept",
    "OCR_APPLIED": "text recovered by OCR",
    "OCR_LOW_CONFIDENCE": "OCR text on this page is uncertain — check it before relying on it",
    # The damaged native text stayed, and the OCR reading was discarded: the
    # page holds no recovered text, so this must not read like OCR_APPLIED.
    "OCR_REJECTED_LOW_CONFIDENCE": "OCR was too uncertain to replace the damaged text on this page",
    # The counts follow in describe(), because the page average alone hides these lines.
    "OCR_MIXED_CONFIDENCE": "some OCR lines on this page are much weaker than the page average",
    # Quotes the discarded text in describe(): a filter that removes content has
    # to show its work, or a document it misjudges fails silently.
    "OCR_NOISE_FILTERED": "unreadable OCR fragments were discarded",
    # Column clustering gets the common two-column scan right and only partly
    # untangles a grid of panels, so where this fires the order is better than
    # the engine's own and not guaranteed correct.
    "OCR_MULTI_COLUMN": "this page was read as multiple columns — check the order of the OCR text",
    "VLM_APPLIED": "the visual model read this page",
    # Carries a reason (truncated / low-yield / empty / error) in describe().
    "VLM_OUTPUT_REJECTED": "the visual model's reading of this page was discarded",
    "VLM_UNAVAILABLE": "the visual model could not run",
    # Aggregated per document: a line per described page would be most of the
    # document on an illustrated one, and says nothing the count does not.
    "VLM_FIGURES_DESCRIBED": "figures were described in prose",
    "VLM_DESCRIBE_FAILED": "some figure pages could not be described",
    # Kept rather than discarded — a description cut short still describes what
    # it reached — but said out loud, because prose that stopped and prose that
    # ended look identical.
    "VLM_DESCRIBE_TRUNCATED": "some figure descriptions ran out of tokens and stop mid-sentence",
    "VLM_DESCRIBE_UNAVAILABLE": "the describing model could not run",
    # An equation the model returned unbalanced is never published as math: a
    # wrong formula that renders is worse than a missing one.
    "FORMULA_REVIEW_REQUIRED": "a formula on this page was dropped as malformed — check the source",
}

# Codes whose sentence ends in a percentage, so the raw detail would repeat it.
_CONFIDENCE_CODES = ("OCR_APPLIED", "OCR_LOW_CONFIDENCE", "OCR_REJECTED_LOW_CONFIDENCE")


def describe(warning: dict) -> str:
    """Return one readable sentence for a warning dict.

    Unknown codes fall back to the code itself, so a new warning is still
    visible rather than silently dropped.
    """
    code = warning.get("code", "")
    text = _TEMPLATES.get(code, code)

    page = warning.get("page")
    prefix = f"Page {page}: " if page is not None else ""

    confidence = warning.get("confidence")
    if code in _CONFIDENCE_CODES and confidence is not None:
        text = f"{text} (confidence {round(confidence * 100)}%)"

    # describe() runs from the Markdown writer and the CLI summary, after
    # extraction already succeeded — a KeyError here would lose a document
    # whose content was extracted correctly, and main.py's blanket handler
    # would report it as an extraction failure, pointing at the wrong cause.
    # So an incomplete warning drops its embellishment rather than raising,
    # exactly as an unknown code falls back to the code itself.
    if code == "OCR_MIXED_CONFIDENCE" and "low_lines" in warning:
        text = (
            f"{text} ({warning['low_lines']} of {warning.get('total_lines', '?')} lines, "
            f"lowest {round(warning.get('lowest', 0.0) * 100)}%)"
        )

    if code == "VLM_APPLIED":
        notes = []
        if warning.get("formulas"):
            notes.append(f"{warning['formulas']} formula(s) recovered")
        if warning.get("prose") == "redundant":
            notes.append("its prose repeated the page text and was dropped")
        if notes:
            text = f"{text} ({'; '.join(notes)})"

    if code in (
        "VLM_FIGURES_DESCRIBED", "VLM_DESCRIBE_FAILED", "VLM_DESCRIBE_TRUNCATED",
    ) and warning.get("pages"):
        text = f"{text} ({warning['pages']} page(s))"

    if code == "VLM_OUTPUT_REJECTED":
        if warning.get("pages"):
            text = (
                f"{warning['pages']} page(s) the visual model read only repeated "
                "text the document already carries, and were discarded"
            )
        elif warning.get("reason"):
            text = f"{text} ({warning['reason']})"

    if code == "FORMULA_REVIEW_REQUIRED" and warning.get("count"):
        text = f"{text} ({warning['count']})"

    if code == "OCR_NOISE_FILTERED" and warning.get("sample"):
        quoted = ", ".join(repr(fragment) for fragment in warning["sample"])
        text = f"{text} ({warning.get('dropped', len(warning['sample']))}: {quoted})"

    detail = warning.get("detail")
    if detail and code not in _CONFIDENCE_CODES:
        text = f"{text} — {detail}"

    return f"{prefix}{text}"
