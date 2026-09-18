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
from extractor.vlm.doctag import ParsedPage, as_display_math, join_split_diacritics, vocabulary
from extractor.vlm.engine import render_page
from extractor.vlm.models import parser_for

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


def _cache_path(
    png_bytes: bytes, model: str, max_tokens: int, penalty: float, prompt: str = "",
) -> Path:
    """Where this page's inference is cached.

    Keyed on the rendered image rather than the source file, so DPI and any
    future render change are already part of the identity; model, token cap and
    repetition penalty are added because they change the output for identical
    pixels — without the penalty in the key, a page cached as a repetition loop
    would be served back forever after the penalty that fixes it is turned on.
    The model is what keeps the describing pass off the reading pass's entries:
    both see the same pixels and answer entirely different questions. The prompt
    is in the key for the same reason one step down — it *is* the question, so
    editing one to fix bad output would otherwise serve the bad output back
    forever, exactly as an unkeyed repetition penalty once would have.
    """
    config = get_config()
    root = Path(config.cache_dir) if config.cache_dir else DEFAULT_CACHE_DIR
    digest = hashlib.sha256(
        png_bytes + f"|{model}|{max_tokens}|{penalty}|{prompt}".encode()
    ).hexdigest()
    return root / f"{digest}.json"


def _infer(
    engine, png_bytes: bytes, *, model: str, max_tokens: int, penalty: float,
) -> tuple[str, bool]:
    """Return ``(raw output, hit the token cap)``, from cache when possible."""
    # From the engine rather than a parameter: every engine carries the prompt
    # it was built with, so both passes are keyed correctly without either
    # caller remembering to pass it.
    path = _cache_path(
        png_bytes, model, max_tokens, penalty, getattr(engine, "prompt", ""),
    )
    if path.exists():
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            # Entries written before the cap was recorded default to False, so
            # an old cache stays usable and only loses the extra signal.
            return entry["raw"], entry.get("capped", False)
        except (OSError, ValueError, KeyError):  # a damaged entry is not fatal
            logger.debug("Ignoring unreadable VLM cache entry: %s", path)

    raw, capped = engine.convert(
        png_bytes, max_tokens=max_tokens, repetition_penalty=penalty,
    )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"raw": raw, "capped": capped}), encoding="utf-8")
    except OSError:  # a read-only cache must not fail the run
        logger.debug("Could not write VLM cache entry: %s", path)
    return raw, capped


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
    native_table_pages: frozenset[int] = frozenset(),
    candidates: frozenset[int] | None = None,
    mismapped_pages: frozenset[int] = frozenset(),
) -> tuple[dict[int, ParsedPage], list[dict]]:
    """Read the pages of *path* the visual model is wanted on.

    Returns ``({page_number: ParsedPage}, warnings)``. Pages absent from the
    mapping were rejected; the warnings say why. Under ``VlmConfig.pages ==
    "all"`` the whole document is routed — the model reads formulas and figure
    structure the native path never sees. Under ``"auto"`` only *candidates*
    (1-based, chosen by the caller from the page signals) are sent; with none,
    the model is not even loaded. The merge rules in ``pdf_reader`` decide
    where its output may displace native content and where it is only added.

    *native_table_pages* is the one merge rule this function has to know about.
    ``pdf_reader`` keeps the model's tables only where pdfplumber found none,
    so on a page that has native tables a redundant-prose reading kept "for its
    tables" contributes nothing at all — and without this it would still be
    reported as ``VLM_APPLIED`` and stay out of the duplicate tally. On a
    table-heavy document that is the common case, not an edge.

    *mismapped_pages* (1-based) are exempt from the numeric formula gate, as
    are the replaced classes: on both the text layer is the damage, not the
    judge. See ``doctag.unsupported_numbers``.
    """
    if not get_config().enabled:
        return {}, []

    targets = list(range(1, len(page_classes) + 1))
    if get_config().pages != "all":
        targets = [page for page in targets if page in (candidates or frozenset())]
    if not targets:
        return {}, []

    if not registry.is_available():
        reason = registry.unavailable_reason()
        return {}, [{
            "code": "VLM_UNAVAILABLE",
            "detail": reason.reason.describe() if reason else "",
            "pages": len(targets),
        }]

    # One parser for the run: the model cannot change between pages, and
    # choosing it here keeps the per-page loop free of format knowledge.
    parser = parser_for(get_config().model)
    # The document's own words decide how the model's split diacritics rejoin;
    # see doctag.join_split_diacritics. Built once: it reads every page.
    vocab = vocabulary(native_texts)

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
        for page_number in targets:
            index = page_number - 1
            replacing = page_classes[index] in REPLACE_CLASSES
            try:
                raw, capped = _infer(
                    engine, render_page(doc[index], dpi=dpi),
                    model=get_config().model,
                    max_tokens=get_config().max_tokens,
                    penalty=get_config().repetition_penalty,
                )
                # Additive page with a sound text layer: its digits judge the
                # model's. Anywhere else there is nothing to judge against.
                trusted = not replacing and page_number not in mismapped_pages
                parsed = parser.parse(raw, native_texts[index] if trusted else None)
                # The cap is evidence the answer was cut off; the parser's own
                # check is whatever extra its format can prove.
                truncated = capped or parser.is_truncated(raw)
                parsed.text = join_split_diacritics(parsed.text, vocab)
                parsed.tables = [
                    [[join_split_diacritics(cell, vocab) for cell in row] for row in table]
                    for table in parsed.tables
                ]
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
                parsed.text = "\n\n".join(
                    as_display_math(f) for f in parsed.formulas
                )
                applied["prose"] = "redundant"
                kept_tables = parsed.tables and page_number not in native_table_pages
                if not parsed.text and not kept_tables:
                    duplicates += 1
                    continue

            results[page_number] = parsed
            warnings.append(applied)
            if parsed.rejected_formulas:
                warnings.append({
                    "code": "FORMULA_REVIEW_REQUIRED", "page": page_number,
                    "count": parsed.rejected_formulas,
                })
            if parsed.unsupported_numbers:
                warnings.append({
                    "code": "VLM_FORMULA_REJECTED", "page": page_number,
                    "numbers": parsed.unsupported_numbers,
                })

    if duplicates:
        warnings.append({
            "code": "VLM_OUTPUT_REJECTED", "reason": "duplicate", "pages": duplicates,
        })

    return results, warnings
