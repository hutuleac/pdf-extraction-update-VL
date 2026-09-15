"""Run-wide visual-model settings, set once from the CLI.

Same shape and reasoning as ``extractor/ocr/config.py`` — readers take only a
path, so options travel through this module rather than every signature.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

DEFAULT_MODEL = "ibm-granite/granite-docling-258M-mlx"
DEFAULT_DPI = 144
DEFAULT_MAX_TOKENS = 4096
# Both models fall into repetition loops on damaged pages, burning the whole
# token budget on one repeated fragment. Measured on a garbled page that looped
# 51 times: unpenalized it ran to the 4096-token cap in 39 s and returned 8096
# characters of " Cl + Cl + "; at 1.05 it stopped on its own after 1382 tokens
# in 12 s with 3666 characters of real content. 1.05, 1.1 and 1.2 all fixed it
# identically, so this is the gentlest setting that works — the penalty falls
# on legitimately repeated tokens too, and a table's repeated headers and
# repeated numeric cells are exactly that. Set 1.0 to disable.
DEFAULT_REPETITION_PENALTY = 1.05


@dataclass(frozen=True)
class VlmConfig:
    """User-facing visual-fallback options for one run."""

    enabled: bool = False
    model: str = DEFAULT_MODEL
    dpi: int = DEFAULT_DPI
    max_tokens: int = DEFAULT_MAX_TOKENS
    repetition_penalty: float = DEFAULT_REPETITION_PENALTY
    # On by default: a 388-page run is ~90 minutes, and an interruption must
    # not cost all of it. None disables caching entirely.
    cache_dir: str | None = None


_config = VlmConfig()


def get_config() -> VlmConfig:
    """Return the settings in force for this run."""
    return _config


def configure(**overrides) -> VlmConfig:
    """Replace the current settings and reset any cached model probe."""
    global _config
    _config = replace(_config, **overrides)
    _reset_probe()
    return _config


def reset() -> VlmConfig:
    """Restore the default settings — used between test cases."""
    global _config
    _config = VlmConfig()
    _reset_probe()
    return _config


def _reset_probe() -> None:
    """Drop the cached engine so the next call re-probes with new settings."""
    from extractor.vlm import registry

    registry.reset()
