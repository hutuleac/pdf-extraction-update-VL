"""Shared glue between the readers and the OCR engine.

Both the PDF reader and the image reader need the same thing: hand over one or
more rasterized images, get back a text block and the warnings that describe
what happened. That logic lives here once.
"""
from __future__ import annotations

import logging

from extractor.model import make_ocr_text_block
from extractor.ocr import registry
from extractor.ocr.base import ICON_PLACEHOLDER, UnavailableReason
from extractor.ocr.config import get_config
from extractor.reading_order import reorder_words

logger = logging.getLogger(__name__)

# Reasons that deserve their own warning code rather than the generic one.
_REASON_CODES = {
    UnavailableReason.DISABLED: "OCR_SKIPPED_DISABLED",
    UnavailableReason.MODEL_INCOMPATIBLE: "OCR_MODEL_INCOMPATIBLE",
}

# A string this short has no internal redundancy, so a sub-threshold read of it
# cannot be verified or recovered from context -- unlike a garbled sentence,
# which keeps enough structure to be worth surfacing. Length alone never
# discards anything: the confidence gate decides, and this only limits how much
# the gate is allowed to throw away. Whatever it does discard is reported, so a
# document that legitimately holds short low-confidence text (form checkboxes,
# element symbols, single-letter table cells) says so instead of going quiet.
MAX_NOISE_FRAGMENT_CHARS = 3

# reading_order.py's thresholds (MIN_COLUMN_GAP and friends) are tuned in PDF
# points, and OCR bboxes arrive in pixels of whatever raster the engine was
# handed — 150 DPI page renders, a phone screenshot, a 2000 px scan. Scaling
# each image's coordinates so its width maps to a nominal page width lets one
# set of constants serve both paths instead of a second set tuned per source.
NOMINAL_PAGE_WIDTH = 612.0


def _in_reading_order(lines: list, width: float) -> tuple[list, bool]:
    """Return ``(lines in column-aware reading order, columns run side by side)``.

    The engine sorts its detections top-to-bottom, which is the right answer on
    a single column and the wrong one on anything laid out side by side: a
    two-column scan comes back with the columns interleaved line by line, and a
    chunker downstream then splits neighbours and glues strangers. Each line is
    handed to ``reorder_words`` as its own block, so the column clustering that
    already serves the PDF text path serves this one too.

    ponytail: column clustering, not layout analysis. Measured on a 2x2 panel
    infographic (148 lines, 4 panels): the engine's own order switched panel 57
    times, this switches 34, and perfect grouping would switch 3. It is a strict
    improvement on every layout and a complete answer only on columns — a grid
    of panels needs region segmentation, which is a different and much larger
    tool. The second return value is why the warning exists: where this matters
    most is exactly where it is least likely to be entirely right.
    """
    if width <= 0 or len(lines) < 2:
        return lines, False
    scale = NOMINAL_PAGE_WIDTH / width
    words = [
        (line.bbox[0] * scale, line.bbox[1] * scale,
         line.bbox[2] * scale, line.bbox[3] * scale,
         line.text, index, 0, 0)
        for index, line in enumerate(lines)
    ]
    ordered, _columns, concurrent = reorder_words(words)
    return [lines[block_no] for _text, block_no, _line_no in ordered], concurrent

# How many discarded fragments the warning quotes back, so a reader can judge
# whether the filter took something it should not have.
_NOISE_SAMPLE_SIZE = 5


def _is_unreadable_fragment(text: str, confidence: float, threshold: float) -> bool:
    """True for a sub-threshold read too short to carry recoverable signal."""
    return confidence < threshold and len(text) <= MAX_NOISE_FRAGMENT_CHARS


def _with_page(warning: dict, page_number: int | None) -> dict:
    """Attach a page number to a warning when the caller knows one."""
    if page_number is not None:
        warning["page"] = page_number
    return warning


def unavailable_warning(page_number: int | None = None) -> dict:
    """Build the warning that explains why a page could not be read."""
    failure = registry.unavailable_reason()
    if failure is None:
        return _with_page({"code": "OCR_UNAVAILABLE"}, page_number)
    code = _REASON_CODES.get(failure.reason, "OCR_UNAVAILABLE")
    return _with_page({"code": code, "detail": str(failure)}, page_number)


def ocr_images(images: list, page_number: int | None = None) -> tuple[dict | None, list[dict]]:
    """OCR every image for one unit and return (text block or None, warnings).

    Never raises: an engine failure downgrades to an ``OCR_FAILED`` warning so
    the remaining pages still get processed.
    """
    if not images:
        return None, []
    if not registry.is_available():
        return None, [unavailable_warning(page_number)]

    engine = registry.get_engine()
    threshold = get_config().min_confidence
    texts: list[str] = []
    line_scores: list[float] = []
    discarded: list[str] = []
    failures: list[str] = []
    multi_column = False

    for image in images:
        try:
            result = engine.recognize(image)
        except Exception as exc:  # noqa: BLE001 - one bad image must not lose the others
            logger.warning("OCR failed on an image on page %s: %s", page_number, exc)
            failures.append(str(exc))
            continue
        width = image.shape[1] if getattr(image, "shape", None) else 0
        ordered, concurrent = _in_reading_order(result.lines, width)
        multi_column = multi_column or concurrent
        for line in ordered:
            if not line.text:
                continue
            if _is_unreadable_fragment(line.text, line.confidence, threshold):
                discarded.append(line.text)
                continue
            texts.append(line.text)
            # The marker is ours, not something the model read, so its score
            # measures nothing about the text and must not move the average.
            if line.text != ICON_PLACEHOLDER:
                line_scores.append(line.confidence)

    # Every image failed: nothing was read at all, so this keeps OCR_FAILED's
    # existing meaning and its place in _UNREADABLE_CODES.
    if failures and len(failures) == len(images):
        return None, [_with_page(
            {"code": "OCR_FAILED", "detail": failures[0]}, page_number,
        )]

    noise_warnings = []
    if failures:
        # Some images failed but others were read: the page still holds text,
        # so this must not be OCR_FAILED (which main.py treats as unreadable).
        noise_warnings.append(_with_page({
            "code": "OCR_IMAGE_FAILED",
            "images": len(failures),
            "detail": failures[0],
        }, page_number))
    if multi_column:
        noise_warnings.append(_with_page({"code": "OCR_MULTI_COLUMN"}, page_number))
    if discarded:
        noise_warnings.append(_with_page({
            "code": "OCR_NOISE_FILTERED",
            "dropped": len(discarded),
            "sample": discarded[:_NOISE_SAMPLE_SIZE],
        }, page_number))

    # Markers and discarded fragments are not text: with nothing scoreable left,
    # the page recovered no reading, however many characters it emitted.
    if not line_scores:
        return None, [_with_page(
            {"code": "OCR_APPLIED", "engine": engine.name, "confidence": 0.0}, page_number,
        ), *noise_warnings]

    # Averaged over lines, not over images: a one-line thumbnail must not carry
    # the same weight as a fifty-line screenshot on the same page.
    confidence = sum(line_scores) / len(line_scores)
    block = make_ocr_text_block("\n".join(texts), confidence)
    warnings = [_with_page(
        {"code": "OCR_APPLIED", "engine": engine.name, "confidence": round(confidence, 3)},
        page_number,
    ), *noise_warnings]
    if confidence < threshold:
        warnings.append(_with_page(
            {"code": "OCR_LOW_CONFIDENCE", "confidence": round(confidence, 3)}, page_number,
        ))
    else:
        # The page average can look reassuring while individual lines are junk —
        # a clean title averages away a garbled chart axis. Report the outliers.
        low = [score for score in line_scores if score < threshold]
        if low:
            warnings.append(_with_page({
                "code": "OCR_MIXED_CONFIDENCE",
                "low_lines": len(low),
                "total_lines": len(line_scores),
                "lowest": round(min(low), 3),
            }, page_number))
    return block, warnings
