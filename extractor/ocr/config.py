"""Run-wide OCR settings, set once from the CLI and read by the readers.

The reader entry points take only a path (see ``dispatcher``), so OCR options
travel through this small module-level holder rather than through every
signature.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

DEFAULT_DPI = 300
DEFAULT_MIN_CONFIDENCE = 0.60

# Hard cap on rasterized pixels per page. 25 MP covers the normal image corpus
# while bounding the largest in-memory RGB frame at roughly 75 MB.
DEFAULT_MAX_PIXELS = 25_000_000


@dataclass(frozen=True)
class OcrConfig:
    """User-facing OCR options for one run."""

    enabled: bool = True
    # Off by default: on a chart-and-map document the figure path returns
    # mostly axis labels and legend fragments (see CHANGELOG Phase 1.5). It is
    # worth turning on for infographic decks, where the figure carries the only
    # text on the page — a 13-page reference deck lost 36% of its characters
    # without it, including a whole SAE-level table.
    figures: bool = False
    model_dir: str | None = None
    dpi: int = DEFAULT_DPI
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    max_pixels: int = DEFAULT_MAX_PIXELS


_config = OcrConfig()


def get_config() -> OcrConfig:
    """Return the settings in force for this run."""
    return _config


def configure(**overrides) -> OcrConfig:
    """Replace the current settings and reset any cached engine probe."""
    global _config
    _config = replace(_config, **overrides)
    _reset_probe()
    return _config


def reset() -> OcrConfig:
    """Restore the default settings — used between test cases."""
    global _config
    _config = OcrConfig()
    _reset_probe()
    return _config


def _reset_probe() -> None:
    """Drop the cached engine so the next call re-probes with new settings."""
    from extractor.ocr import registry

    registry.reset()
