"""PDF page classification from cheap signals — pure functions, no I/O.

Computes per-page metrics from PyMuPDF page objects and classifies each page
into one of: native-text, scanned, mixed, garbled, layout-complex.

All thresholds are module constants so they can be tuned without touching logic.
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Minimum text characters to consider the page as having meaningful text
MIN_TEXT_CHARS = 20

# If image area covers more than this fraction of the page, images are dominant
IMAGE_AREA_RATIO_HIGH = 0.60

# If replacement chars (U+FFFD) or non-printable ratio exceeds this, text is garbled
GARBLED_RATIO_THRESHOLD = 0.05

# More than this many distinct fonts suggests layout complexity
FONT_COUNT_HIGH = 12

# More than this many ruling lines (horizontal + vertical) suggests layout complexity
RULING_LINE_COUNT_HIGH = 30

# Scripts a document in this pipeline's languages legitimately uses. Greek is
# here because maths is written in it; Cyrillic because a Latin-script document
# quotes it without anything being wrong.
_EXPECTED_SCRIPTS = ("LATIN", "GREEK", "CYRILLIC", "COMBINING", "MODIFIER")

# How many *distinct* unexpected scripts on one page mean mismapped glyphs
# rather than a quotation. Two is the whole discriminator: a document quoting a
# foreign phrase uses one script, while a symbol font decoded through a broken
# ToUnicode CMap scatters glyphs across unrelated blocks at once — NKO beside
# Malayalam beside Syriac, which no real document contains. Measured on the
# 388-page reference course: 314 of 334 text pages score zero, and every page
# that trips this holds damaged formulas.
MISMAPPED_SCRIPT_COUNT = 2


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PageSignals:
    """Raw numeric signals extracted from a single PDF page."""

    text_chars: int = 0
    word_count: int = 0
    printable_ratio: float = 1.0
    replacement_ratio: float = 0.0
    image_area_ratio: float = 0.0
    ruling_line_count: int = 0
    font_count: int = 0
    mismapped_scripts: tuple[str, ...] = ()


def _unexpected_scripts(text: str) -> tuple[str, ...]:
    """Names of the scripts in *text* that a real document would not mix in.

    Letters and combining marks only: a maths symbol or a dash carries no
    script, and including punctuation made this fire on ordinary pages.
    """
    found = set()
    for char in text:
        # Below U+0370 there is no script but Latin — Latin-1, the Latin
        # extensions, IPA and the combining diacriticals. The name prefix is
        # not a script there and reading it as one is wrong: `º` is
        # MASCULINE ORDINAL INDICATOR, an ordinary Romanian and Spanish
        # character, and `µ` is MICRO SIGN.
        if ord(char) < 0x0370 or unicodedata.category(char)[0] not in "LM":
            continue
        try:
            script = unicodedata.name(char).split()[0]
        except ValueError:
            continue
        if script not in _EXPECTED_SCRIPTS:
            found.add(script)
    return tuple(sorted(found))


def compute_signals(page) -> PageSignals:
    """Compute classification signals from a PyMuPDF page object.

    This function uses only PyMuPDF APIs (no new deps). The *page* parameter
    is a ``pymupdf.Page`` instance.
    """
    # --- text metrics ---
    text = page.get_text("text") or ""
    text_chars = len(text)
    words = page.get_text("words") or []
    word_count = len(words)

    # Printable / replacement ratio
    if text_chars > 0:
        replacement_count = text.count("\ufffd")
        non_printable = sum(
            1 for ch in text
            if not ch.isprintable() and ch not in ("\n", "\r", "\t", "\f")
        )
        replacement_ratio = replacement_count / text_chars
        printable_ratio = 1.0 - (non_printable / text_chars)
    else:
        replacement_ratio = 0.0
        printable_ratio = 1.0

    # --- mismapped symbol-font glyphs ---
    mismapped_scripts = _unexpected_scripts(text)

    # --- image area ratio ---
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height if page_rect else 1.0
    images = page.get_images(full=True) or []
    image_area = 0.0
    for img in images:
        # img tuple: (xref, smask, width, height, bpc, colorspace, ...)
        # We approximate with image pixel dimensions vs page dimensions.
        # A more precise approach would use image bboxes but that requires
        # page.get_image_rects() which may not always succeed.
        try:
            img_rects = page.get_image_rects(img[0])
            for rect in img_rects:
                image_area += rect.width * rect.height
        except Exception as exc:  # noqa: BLE001
            # Skip this image's area contribution, but leave a trace: a page
            # whose rects consistently fail is classified on a ratio of 0, and
            # silence makes that misclassification impossible to explain.
            logger.debug("Image rects unavailable for xref %s: %s", img[0], exc)

    image_area_ratio = min(image_area / page_area, 1.0) if page_area > 0 else 0.0

    # --- ruling lines (drawings) ---
    drawings = page.get_drawings() or []
    ruling_line_count = 0
    for d in drawings:
        # Each drawing has items; lines are ('l', p1, p2) or ('re', rect)
        for item in d.get("items", []):
            if item[0] == "l":
                ruling_line_count += 1
            elif item[0] == "re":
                ruling_line_count += 4  # rectangle = 4 lines

    # --- font count ---
    fonts: set[str] = set()
    blocks = page.get_text("dict", flags=0).get("blocks", [])
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                if font_name:
                    fonts.add(font_name)
    font_count = len(fonts)

    return PageSignals(
        text_chars=text_chars,
        word_count=word_count,
        printable_ratio=printable_ratio,
        replacement_ratio=replacement_ratio,
        image_area_ratio=image_area_ratio,
        ruling_line_count=ruling_line_count,
        font_count=font_count,
        mismapped_scripts=mismapped_scripts,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_page(signals: PageSignals) -> str:
    """Classify a page based on its signals.

    Returns one of:
      - 'native-text'    : normal text page
      - 'scanned'        : image-dominant, negligible text (likely a scan)
      - 'mixed'          : meaningful text plus large image coverage
      - 'garbled'        : high replacement/non-printable ratio
      - 'layout-complex' : many ruling lines or many font variants

    The order of checks encodes priority (garbled > scanned > mixed >
    layout-complex > native-text).
    """
    # Garbled takes priority — unreliable text
    if signals.replacement_ratio > GARBLED_RATIO_THRESHOLD:
        return "garbled"
    if signals.printable_ratio < (1.0 - GARBLED_RATIO_THRESHOLD):
        return "garbled"

    # Scanned: images dominate, negligible text
    if signals.image_area_ratio > IMAGE_AREA_RATIO_HIGH and signals.text_chars < MIN_TEXT_CHARS:
        return "scanned"

    # Mixed: meaningful text AND large image coverage
    if signals.image_area_ratio > IMAGE_AREA_RATIO_HIGH and signals.text_chars >= MIN_TEXT_CHARS:
        return "mixed"

    # Layout-complex: many rules or many fonts
    if signals.ruling_line_count > RULING_LINE_COUNT_HIGH:
        return "layout-complex"
    if signals.font_count > FONT_COUNT_HIGH:
        return "layout-complex"

    return "native-text"


def warnings_for_page(signals: PageSignals, page_class: str, page_number: int) -> list[dict]:
    """Return warning dicts appropriate for the given page classification."""
    warnings: list[dict] = []

    if page_class == "scanned":
        warnings.append({"code": "SCANNED_PAGE_NO_TEXT", "page": page_number})
    elif page_class == "mixed":
        warnings.append({"code": "MIXED_CONTENT_PAGE", "page": page_number})
    elif page_class == "garbled":
        warnings.append({"code": "GARBLED_TEXT", "page": page_number})
    elif page_class == "layout-complex":
        warnings.append({"code": "LAYOUT_COMPLEX", "page": page_number})

    # Independent of the class, and deliberately not a reclassification. These
    # pages read correctly as prose — the damage is concentrated in formulas and
    # symbols, a handful of characters in a page of sound text. Calling them
    # `garbled` would send them down the replace path and bet a thousand good
    # characters against a model reading to recover seven bad ones.
    if len(signals.mismapped_scripts) >= MISMAPPED_SCRIPT_COUNT:
        warnings.append({
            "code": "MISMAPPED_GLYPHS", "page": page_number,
            "scripts": list(signals.mismapped_scripts),
        })

    return warnings
