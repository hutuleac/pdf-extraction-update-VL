"""OCR value types and the engine contract — no heavy imports here.

This module must stay importable on a machine with no OCR dependencies at all,
so the rest of the pipeline can talk about OCR results without paying for
onnxruntime, opencv or numpy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

# A Chinese-capable recognition model has no "not text" class: an ambiguous
# icon glyph (a sort arrow, a folder icon) still gets forced into the closest
# CJK ideograph shape it knows. A single isolated CJK character -- nothing
# else recognized in that line -- is almost always this failure mode rather
# than genuine content, so it is swapped for a marker a reader can act on.
# It lives here rather than beside the decoder because the glue layer has to
# recognize it too, and must not import numpy to do so.
ICON_PLACEHOLDER = "[icon]"


class UnavailableReason(StrEnum):
    """Why OCR cannot run, in words a user can act on."""

    DISABLED = "disabled"
    MISSING_DEPS = "missing-deps"
    MISSING_MODEL_DIR = "missing-model-dir"
    MISSING_DET_MODEL = "missing-det-model"
    MISSING_REC_MODEL = "missing-rec-model"
    MISSING_DICT = "missing-dict"
    MODEL_INCOMPATIBLE = "model-incompatible"
    LOAD_FAILED = "load-failed"

    def describe(self) -> str:
        """Return a plain-language explanation of this reason."""
        return _REASON_TEXT[self]


_REASON_TEXT: dict[UnavailableReason, str] = {
    UnavailableReason.DISABLED: "OCR was turned off with --no-ocr",
    UnavailableReason.MISSING_DEPS: (
        'OCR dependencies are not installed — run: pip install -e ".[ocr]"'
    ),
    UnavailableReason.MISSING_MODEL_DIR: "no OCR model directory was found",
    UnavailableReason.MISSING_DET_MODEL: "the text-detection model file is missing",
    UnavailableReason.MISSING_REC_MODEL: "the text-recognition model file is missing",
    UnavailableReason.MISSING_DICT: "the character dictionary is missing",
    UnavailableReason.MODEL_INCOMPATIBLE: (
        "the model does not match the character dictionary — decoding was refused"
    ),
    UnavailableReason.LOAD_FAILED: "the OCR model could not be loaded",
}


class OcrUnavailable(Exception):
    """Raised when an OCR engine is requested but cannot be provided."""

    def __init__(self, reason: UnavailableReason, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        message = reason.describe()
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


@dataclass(frozen=True)
class OcrLine:
    """One recognized line of text with its position on the page image."""

    text: str
    confidence: float
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class OcrResult:
    """Everything one OCR pass produced for one image."""

    lines: list[OcrLine]
    engine: str

    def text(self) -> str:
        """Join the recognized lines into a single newline-separated string."""
        return "\n".join(line.text for line in self.lines if line.text)

    def scores(self) -> list[float]:
        """Confidence of each non-empty line, for the caller to aggregate.

        Deliberately not an average: a page pools the lines from several images,
        and pre-averaging per image would let a one-line thumbnail outvote a
        fifty-line screenshot.
        """
        return [line.confidence for line in self.lines if line.text]


@runtime_checkable
class OcrEngine(Protocol):
    """The single method every engine must provide."""

    name: str

    def recognize(self, image) -> OcrResult:
        """Recognize text in an HxWx3 uint8 RGB image array."""
        ...
