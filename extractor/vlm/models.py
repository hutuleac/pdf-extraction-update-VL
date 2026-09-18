"""Which visual model speaks which format, and how to read it back.

granite-docling emits a ``<doctag>`` tag stream. The prompt that gets a model
to produce its format and the parser that reads that format back are one
choice, not two, so they live in a single table rather than in two places
keyed on the same string. PaddleOCR-VL had an entry here until 2026-09-18; it
is an element recognizer built to run behind a layout detector, and fed whole
pages it ran past an 8192-token cap on half of them. See
docs/backlog-formulas-and-models.md.

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
# suffix ("granite-docling-258M-mlx-4bit") resolves without a new entry.
SPECS: dict[str, ModelSpec] = {
    "granite-docling": ModelSpec(
        # Not a knob: a free-form instruction produces prose, not the tag
        # stream doctag.py parses.
        prompt="Convert this page to docling.",
        parser="extractor.vlm.doctag",
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
