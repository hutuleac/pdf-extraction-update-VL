"""Say what a page's figures *show*, in prose Phase 2 can embed.

The third reader, and the only one that produces content no other path can.
The native text layer holds a chart's axis labels and nothing about the chart;
OCR under ``--ocr-figures`` returns those same labels as fragments (68% of them
≤3 chars on the measured course); the document-conversion model emits
``<picture>`` and moves on. None of them answers "what does this figure show".

Two things this is deliberately *not*:

It is not an entry in ``models.py``. That table maps a model to a
page-conversion prompt and the parser that reads its format back, and
``--vlm-model`` picks one model for the whole reading path. Description is
additive — it runs beside granite rather than instead of it, and there is
nothing to parse, because the output is the paragraph. A second dispatch axis
inside a table built for one would buy nothing.

It is not region-based. Cropping to each figure was the obvious design and it
does not survive the corpus: ``raster.page_image_regions`` finds zero regions
on 16 of 17 pages of the reference deck, whose figures are vector art with no
embedded raster, and the conversion model's own ``<picture>`` boxes there are
12x11 and 22x19 units — logos, not diagrams, absent entirely on two pages that
have figures. Whole page is both the smaller change and the one that works.

The model announces a figureless page itself: asked for graphics on a page that
has none, it answers ``NONE`` rather than inventing a description. That is the
gate — no output heuristic, no region signal to threshold. It is a prompt
instruction, so it is measured rather than trusted: on the pages probed it fired
on exactly the text-only ones.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pymupdf

from extractor.vlm.apply import _infer
from extractor.vlm.base import UnavailableReason, VlmUnavailable
from extractor.vlm.config import get_config
from extractor.vlm.engine import render_page

logger = logging.getLogger(__name__)

# Mirrors pdf_reader.FIGURE_OCR_CLASSES rather than importing it: the reader
# imports this module, and apply.py already keeps its own copy of the class
# names for the same reason.
FIGURE_CLASSES = ("mixed", "layout-complex")

# "Describe only the graphics, not the page's body text" is the load-bearing
# half. Without it the model returns the page's prose back — the same
# duplication MIN_PROSE_NOVELTY exists to catch on the reading path, and here
# there would be no novelty check to catch it.
#
# The absence clause earns its place too: listing the kinds of graphic invites
# the model to report on the ones that are missing, and a real description of a
# product drawing closed with "No axes, charts, maps, or photographs are
# present." That sentence is the prompt's own vocabulary echoed back as a
# finding — harmless to read and pure noise to embed. The prompt is part of the
# inference cache key, so editing it re-reads the pages rather than serving the
# old answer back.
PROMPT = (
    "Describe each figure, chart, diagram, map or photograph on this page: "
    "what it depicts, the quantities on its axes, its labelled parts, and "
    "the relationship or process it conveys. Write short prose. Describe "
    "only the graphics, not the page's body text. "
    "Describe only what is present — never state that a kind of graphic is "
    "absent. "
    "If the page has no graphics at all, reply with the single word NONE."
)

NO_FIGURES = "NONE"

_engine = None
_failure: VlmUnavailable | None = None
_probed = False


def reset() -> None:
    """Forget the cached engine — used when configuration changes, and in tests."""
    global _engine, _failure, _probed
    _engine, _failure, _probed = None, None, False


def _get_engine():
    """Return the describing engine, or raise VlmUnavailable saying why not.

    Its own probe rather than ``registry``'s: that one caches a single engine
    for ``config.model``, and this is a second model loaded beside it.
    """
    global _engine, _failure, _probed
    if not _probed:
        _probed = True
        try:
            from extractor.vlm.engine import MlxVlmEngine

            _engine = MlxVlmEngine(get_config().describe_model, PROMPT)
        except Exception as exc:  # noqa: BLE001 - never break extraction
            _failure = VlmUnavailable(UnavailableReason.MODEL_LOAD_FAILED, str(exc))
            logger.debug("Describing model unavailable: %s", exc)
    if _engine is None:
        raise _failure or VlmUnavailable(UnavailableReason.MODEL_LOAD_FAILED)
    return _engine


def _describe(engine, png_bytes: bytes) -> tuple[str | None, bool]:
    """Return ``(description or None for no figures, hit the token cap)``."""
    config = get_config()
    raw, capped = _infer(
        engine, png_bytes,
        model=config.describe_model,
        max_tokens=config.describe_max_tokens,
        # Prose, not a tag stream: the loops the penalty exists for are a
        # conversion-model failure, and penalizing repeated tokens in a
        # paragraph costs more than it saves.
        penalty=1.0,
    )
    text = raw.strip()
    if not text or text.upper().rstrip(".") == NO_FIGURES:
        return None, capped
    return text, capped


def describe_image(png_bytes: bytes) -> tuple[str | None, list[dict]]:
    """Describe the figures in a standalone image file.

    Additive only, and deliberately not accompanied by a reading pass. An image
    file's text comes from OCR, which is *trusted* output — unlike the native
    text of a `scanned` PDF page, which is why that path lets the model replace
    it. Handing the same image to a conversion model would be betting verified
    text against an unmeasured reading, and would put the "who wins here" rule
    in a second place besides ``pdf_reader``.

    It also does not consult ``registry``: that probe is for the *reading*
    model, and on this path there is none. Asking it would load granite's
    weights purely to decide whether a different model can run.
    """
    config = get_config()
    if not (config.enabled and config.describe):
        return None, []
    try:
        engine = _get_engine()
    except VlmUnavailable as exc:
        return None, [{
            "code": "VLM_DESCRIBE_UNAVAILABLE", "detail": exc.reason.describe(), "pages": 1,
        }]
    try:
        text, _capped = _describe(engine, png_bytes)
    except Exception as exc:  # noqa: BLE001 - a failed description is not a failed file
        logger.warning("Figure description failed on image: %s", exc)
        return None, [{"code": "VLM_DESCRIBE_FAILED", "pages": 1}]
    if text is None:
        return None, []
    return text, [{"code": "VLM_FIGURES_DESCRIBED", "pages": 1}]


def describe_pages(
    path: Path, page_classes: list[str], page_image_counts: list[int],
) -> tuple[dict[int, str], list[dict]]:
    """Return ``({page number: description}, warnings)`` for *path*'s figures.

    A page is sent when it is a ``FIGURE_CLASSES`` page *or* carries an
    embedded image. The class alone is not "has figures": the reference
    course's seismic-zoning page — a full-page map of Romania contoured by
    ground acceleration, the single most describable page measured — classifies
    as ``native-text``, because the classifier weighs text density and that page
    has a caption-heavy layout. The image count is the same signal ``_ocr_pages``
    already gates its figure pass on, so this adds no new heuristic. Pages the
    model reports as figureless are absent from the mapping.
    """
    config = get_config()
    if not (config.enabled and config.describe):
        return {}, []

    from extractor.vlm import registry

    if not registry.is_available():
        # The reading path already reports VLM_UNAVAILABLE with the reason;
        # repeating it here would print the same failure twice.
        return {}, []

    candidates = [
        index for index, cls in enumerate(page_classes)
        if cls in FIGURE_CLASSES or page_image_counts[index] > 0
    ]
    if not candidates:
        return {}, []

    try:
        engine = _get_engine()
    except VlmUnavailable as exc:
        return {}, [{
            "code": "VLM_DESCRIBE_UNAVAILABLE",
            "detail": exc.reason.describe(),
            "pages": len(candidates),
        }]

    results: dict[int, str] = {}
    failures = 0
    truncated = 0
    with pymupdf.open(path) as doc:
        for index in candidates:
            page_number = index + 1
            try:
                text, capped = _describe(
                    engine, render_page(doc[index], dpi=config.dpi),
                )
            except Exception as exc:  # noqa: BLE001 - isolate one page's failure
                logger.warning("Figure description failed on page %d: %s", page_number, exc)
                failures += 1
                continue
            if text is not None:
                results[page_number] = text
                # Kept, not rejected: a description cut off after two figures
                # still describes two figures, where a formula cut in half is
                # wrong rather than short. But it is reported, because the
                # reader cannot tell prose that stopped from prose that ended
                # — the same reason markdown_doc.is_truncated defers to the cap.
                truncated += capped

    warnings = []
    if results:
        warnings.append({"code": "VLM_FIGURES_DESCRIBED", "pages": len(results)})
    if truncated:
        warnings.append({"code": "VLM_DESCRIBE_TRUNCATED", "pages": truncated})
    if failures:
        warnings.append({"code": "VLM_DESCRIBE_FAILED", "pages": failures})
    return results, warnings
