"""Encoding-aware text file loader using charset-normalizer.

Provides a single helper that reads a binary file, detects its encoding, and
returns decoded text alongside metadata useful for warning emission.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

from charset_normalizer import from_bytes

logger = logging.getLogger(__name__)

# charset-normalizer reports `chaos` (ratio of bytes decoding into implausible
# sequences) and `coherence` (how well the text matches a known language).
# Neither is an encoding confidence on its own: coherence is 0.0 for ANY pure
# ASCII file, and chaos is ~0.0 even for a legacy codepage that decoded fine.
# ENCODING_FALLBACK means "we had to guess", so it is driven by both — the
# encoding is not one the bytes declare, or the decode still looks implausible.
_MAX_CLEAN_CHAOS = 0.05
_CERTAIN_ENCODINGS = ("ascii", "utf_8")

# Default fallback when detection fails entirely
_FALLBACK_ENCODING = "utf-8"


@dataclass
class LoadResult:
    """Result of loading a text file with encoding detection.

    ``confidence`` is ``1 - chaos``: how plausible the decoded bytes look under
    the chosen encoding. It is reported for information and does not on its own
    decide the warning — see the note above ``_MAX_CLEAN_CHAOS``.
    """

    text: str
    encoding: str
    confidence: float
    warnings: list[dict] = field(default_factory=list)


def load_text_file(path: Path) -> LoadResult:
    """Read *path* as bytes, detect encoding, decode, and return a LoadResult.

    When the encoding had to be guessed — a legacy codepage was inferred, or the
    decode still produced implausible sequences — the text is still returned
    using the best guess (or utf-8 fallback), with an ENCODING_FALLBACK warning.
    """
    raw = path.read_bytes()

    # Empty file — nothing to detect
    if not raw:
        return LoadResult(text="", encoding="utf-8", confidence=1.0)

    results = from_bytes(raw)
    best = results.best()

    if best is None:
        # Total detection failure — fall back to utf-8 with replacement
        text = raw.decode(_FALLBACK_ENCODING, errors="replace")
        logger.warning("Encoding detection failed for %s, falling back to %s", path.name, _FALLBACK_ENCODING)
        return LoadResult(
            text=text,
            encoding=_FALLBACK_ENCODING,
            confidence=0.0,
            warnings=[{"code": "ENCODING_FALLBACK", "detail": "detection failed entirely"}],
        )

    encoding = str(best.encoding)
    chaos = float(best.chaos)
    confidence = 1.0 - chaos
    text = str(best)

    warnings: list[dict] = []
    guessed = encoding not in _CERTAIN_ENCODINGS or chaos > _MAX_CLEAN_CHAOS
    if guessed:
        logger.debug(
            "Guessed encoding %s (confidence %.2f) for %s",
            encoding, confidence, path.name,
        )
        warnings.append({
            "code": "ENCODING_FALLBACK",
            "detail": f"detected {encoding} with confidence {confidence:.2f}",
        })

    return LoadResult(text=text, encoding=encoding, confidence=confidence, warnings=warnings)
