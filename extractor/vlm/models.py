"""Which visual model speaks which format, and how to read it back.

Two models, two output languages: granite-docling emits a ``<doctag>`` tag
stream, PaddleOCR-VL emits Markdown. The prompt that gets a model to produce
its format and the parser that reads that format back are one choice, not two,
so they live in a single table rather than in two places keyed on the same
string.

An unrecognized name is refused rather than guessed at. Falling through to the
wrong parser produces an empty ``ParsedPage`` for every page and reports
``VLM_OUTPUT_REJECTED: empty`` across the whole document with no hint why —
silently-wrong output, which this pipeline never trades for a working run.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from extractor.vlm.base import UnavailableReason, VlmUnavailable


@dataclass(frozen=True)
class ModelSpec:
    """How to drive one visual model and how to read its answer."""

    prompt: str
    # Import path rather than the module itself: this table is read by
    # registry.py during the availability probe, and a parser import there
    # would drag regex modules into a probe that may be about to fail.
    parser: str


# Keyed by a substring of the model name, so a quantization or a revision
# suffix ("PaddleOCR-VL-1.6-4bit") resolves without a new entry.
SPECS: dict[str, ModelSpec] = {
    "granite-docling": ModelSpec(
        # Not a knob: a free-form instruction produces prose, not the tag
        # stream doctag.py parses.
        prompt="Convert this page to docling.",
        parser="extractor.vlm.doctag",
    ),
    "paddleocr-vl": ModelSpec(
        # The model card's own measurements: this default scores 1.00 field
        # accuracy, while an explicit "markdown table" instruction scores 0.88.
        prompt="Transcribe this document to markdown.",
        parser="extractor.vlm.markdown_doc",
    ),
}


def for_model(name: str) -> ModelSpec:
    """Return the spec for *name*, or raise VlmUnavailable naming the known ones."""
    lowered = name.lower()
    for key, spec in SPECS.items():
        if key in lowered:
            return spec
    raise VlmUnavailable(
        UnavailableReason.UNKNOWN_MODEL,
        f"{name} — known: {', '.join(sorted(SPECS))}",
    )


def parser_for(name: str) -> ModuleType:
    """Return the module that translates *name*'s output into a ParsedPage."""
    import importlib

    return importlib.import_module(for_model(name).parser)
