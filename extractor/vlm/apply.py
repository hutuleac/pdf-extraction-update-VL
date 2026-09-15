"""The glue every reader calls: infer, cache, judge.

Mirrors ``extractor/ocr/apply.py`` — readers get back content plus warnings and
never talk to the model directly. Each page is inferred in isolation, so one
failure cannot discard the pages already read.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import pymupdf

from extractor.vlm import registry
from extractor.vlm.config import get_config
from extractor.vlm.doctag import ParsedPage, parse
from extractor.vlm.engine import render_page

logger = logging.getLogger(__name__)

# Page classes whose native text is missing or untrustworthy. On these the
# model's reading *replaces* the page text, so it has to carry the whole page;
# everywhere else its output is added beside text that is already correct.
REPLACE_CLASSES = ("scanned", "garbled")

# When replacing, the model must return at least this fraction of the native
# character count. Guards the spike's page-6 failure: a table of contents came
# back 93% empty because the model called the whole page one table and stopped.
# Never applied to an additive page — there a partial reading is still a gain,
# and rejecting it would throw away the formulas that are the point.
MIN_YIELD_RATIO = 0.5

# Below this share of new words, the model's prose is a re-reading of text the
# document already carries. Measured on two reference PDFs: on healthy pages it
# came back 92-100% identical to the native text, which doubles the document
# for nothing. Such a page keeps its formulas and drops the prose.
MIN_PROSE_NOVELTY = 0.30

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "knowledge-extractor" / "vlm"


def _words(text: str) -> set[str]:
    """Comparable words of *text* — short tokens carry no evidence either way."""
    return {word for word in re.findall(r"\w+", text.lower()) if len(word) > 3}


def prose_novelty(vlm_text: str, native_text: str) -> float:
    """Share of the model's words that the native text does not already have."""
    produced = _words(vlm_text)
    if not produced:
        return 0.0
    return len(produced - _words(native_text)) / len(produced)


def _cache_path(png_bytes: bytes) -> Path:
    """Where this page's inference is cached.

    Keyed on the rendered image rather than the source file, so DPI and any
    future render change are already part of the identity; model and token cap
    are added because they change the output for identical pixels.
    """
    config = get_config()
    root = Path(config.cache_dir) if config.cache_dir else DEFAULT_CACHE_DIR
    digest = hashlib.sha256(
        png_bytes + f"|{config.model}|{config.max_tokens}".encode()
    ).hexdigest()
    return root / f"{digest}.json"


def _infer(engine, png_bytes: bytes) -> str:
    """Return raw doctag for one page, from cache when possible."""
    path = _cache_path(png_bytes)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))["raw"]
        except (OSError, ValueError, KeyError):  # a damaged entry is not fatal
            logger.debug("Ignoring unreadable VLM cache entry: %s", path)

    raw = engine.convert(png_bytes, max_tokens=get_config().max_tokens)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"raw": raw}), encoding="utf-8")
    except OSError:  # a read-only cache must not fail the run
        logger.debug("Could not write VLM cache entry: %s", path)
    return raw


def _judge(
    parsed: ParsedPage, truncated: bool, native_chars: int, *, replacing: bool,
) -> str | None:
    """Return a rejection reason, or None when the output is acceptable."""
    if truncated:
        return "truncated"
    produced = len(parsed.text) + sum(
        len(cell) for table in parsed.tables for row in table for cell in row
    )
    if not produced:
        return "empty"
    if replacing and produced < MIN_YIELD_RATIO * native_chars:
        return "low-yield"
    return None


def vlm_pages(
    path: Path, page_classes: list[str], native_texts: list[str],
) -> tuple[dict[int, ParsedPage], list[dict]]:
    """Read every page of *path* with the visual model.

    Returns ``({page_number: ParsedPage}, warnings)``. Pages absent from the
    mapping were rejected; the warnings say why. The whole document is routed
    — the model reads formulas and figure structure the native path never
    sees — and the merge rules in ``pdf_reader`` decide where its output may
    displace native content and where it is only added.
    """
    if not get_config().enabled:
        return {}, []

    if not registry.is_available():
        reason = registry.unavailable_reason()
        return {}, [{
            "code": "VLM_UNAVAILABLE",
            "detail": reason.reason.describe() if reason else "",
            "pages": len(page_classes),
        }]

    from extractor.vlm.doctag import is_truncated

    engine = registry.get_engine()
    dpi = get_config().dpi
    results: dict[int, ParsedPage] = {}
    warnings: list[dict] = []
    # Counted, not listed per page. On a text document most pages are a repeat
    # of text the reader already has, so a line each would bury the handful of
    # pages where something actually went wrong under a page-by-page report of
    # the system working as intended.
    duplicates = 0

    with pymupdf.open(path) as doc:
        for index, page_class in enumerate(page_classes):
            page_number = index + 1
            replacing = page_class in REPLACE_CLASSES
            try:
                raw = _infer(engine, render_page(doc[index], dpi=dpi))
                parsed = parse(raw)
                truncated = is_truncated(raw)
            except Exception as exc:  # noqa: BLE001 - isolate one page's failure
                logger.warning("Visual fallback failed on page %d: %s", page_number, exc)
                warnings.append({
                    "code": "VLM_OUTPUT_REJECTED", "page": page_number, "reason": "error",
                })
                continue

            rejection = _judge(
                parsed, truncated, len(native_texts[index]), replacing=replacing,
            )
            if rejection:
                warnings.append({
                    "code": "VLM_OUTPUT_REJECTED", "page": page_number, "reason": rejection,
                })
                continue

            applied = {
                "code": "VLM_APPLIED", "page": page_number,
                "formulas": parsed.formula_count,
            }
            if not replacing and (
                prose_novelty(parsed.text, native_texts[index]) < MIN_PROSE_NOVELTY
            ):
                # The model re-read text the document already carries. Keeping
                # it would print the page twice; keeping only the equations
                # keeps the one thing the text layer does not hold.
                parsed.text = "\n\n".join(f"$$\n{f}\n$$" for f in parsed.formulas)
                applied["prose"] = "redundant"
                if not parsed.text and not parsed.tables:
                    duplicates += 1
                    continue

            results[page_number] = parsed
            warnings.append(applied)
            if parsed.rejected_formulas:
                warnings.append({
                    "code": "FORMULA_REVIEW_REQUIRED", "page": page_number,
                    "count": parsed.rejected_formulas,
                })

    if duplicates:
        warnings.append({
            "code": "VLM_OUTPUT_REJECTED", "reason": "duplicate", "pages": duplicates,
        })

    return results, warnings
