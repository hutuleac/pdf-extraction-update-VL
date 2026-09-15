"""VLM value types — no heavy imports here.

Mirrors ``extractor/ocr/base.py``: this module must stay importable on a
machine with no VLM dependencies and on any platform, so the rest of the
pipeline can talk about visual-model results without paying for mlx.
"""
from __future__ import annotations

from enum import StrEnum


class UnavailableReason(StrEnum):
    """Why the visual model cannot run, in words a user can act on."""

    DISABLED = "disabled"
    MISSING_DEPS = "missing-deps"
    UNSUPPORTED_PLATFORM = "unsupported-platform"
    MODEL_LOAD_FAILED = "model-load-failed"
    UNKNOWN_MODEL = "unknown-model"

    def describe(self) -> str:
        """Return a plain-language explanation of this reason."""
        return _REASON_TEXT[self]


_REASON_TEXT: dict[UnavailableReason, str] = {
    UnavailableReason.DISABLED: "the visual fallback was not enabled — pass --vlm",
    UnavailableReason.MISSING_DEPS: (
        'visual-model dependencies are not installed — run: pip install -e ".[vlm]"'
    ),
    UnavailableReason.UNSUPPORTED_PLATFORM: (
        "the visual model runs on Apple Silicon only (mlx)"
    ),
    UnavailableReason.MODEL_LOAD_FAILED: "the visual model could not be loaded",
    UnavailableReason.UNKNOWN_MODEL: (
        "--vlm-model names a model whose output format this pipeline cannot read"
    ),
}


class VlmUnavailable(Exception):
    """Raised when a visual model is requested but cannot be provided."""

    def __init__(self, reason: UnavailableReason, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.describe()}{f' ({detail})' if detail else ''}")
