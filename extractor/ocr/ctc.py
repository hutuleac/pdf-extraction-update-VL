"""CTC greedy decoding — logits to text, with no model dependency.

Kept free of onnxruntime so it can be unit-tested on any machine.
"""
from __future__ import annotations

import numpy as np

from extractor.ocr.base import ICON_PLACEHOLDER

BLANK_INDEX = 0

_CJK_IDEOGRAPH_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
)


def is_isolated_cjk_glyph(text: str) -> bool:
    """True for a single CJK ideograph and nothing else."""
    return len(text) == 1 and any(lo <= ord(text) <= hi for lo, hi in _CJK_IDEOGRAPH_RANGES)


def clean_icon_noise(text: str) -> str:
    """Replace a lone misread icon glyph with a readable marker."""
    return ICON_PLACEHOLDER if is_isolated_cjk_glyph(text) else text


def _to_probabilities(logits: np.ndarray) -> np.ndarray:
    """Return per-class probabilities, applying softmax only if needed.

    PaddleOCR recognition models already emit softmax output; a raw-logit
    export would not, and silently decoding those gives wrong confidences.
    """
    if logits.min() >= 0.0 and logits.max() <= 1.0:
        return logits
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


def decode_greedy(logits: np.ndarray, labels: list[str]) -> tuple[str, float]:
    """Decode one (T, C) logits array into (text, mean confidence).

    Repeated classes are collapsed and blanks dropped, as CTC requires.
    Confidence is the mean probability of the characters actually kept.
    """
    if logits.ndim != 2:
        raise ValueError(f"expected a (T, C) array, got shape {logits.shape}")
    if logits.shape[1] != len(labels):
        raise ValueError(
            f"model emits {logits.shape[1]} classes but the dictionary has {len(labels)}"
        )

    probs = _to_probabilities(logits)
    indices = probs.argmax(axis=1)
    scores = probs.max(axis=1)

    chars: list[str] = []
    kept: list[float] = []
    previous = -1
    for position, index in enumerate(indices):
        if index != previous and index != BLANK_INDEX:
            chars.append(labels[index])
            kept.append(float(scores[position]))
        previous = int(index)

    if not chars:
        return "", 0.0
    return "".join(chars), sum(kept) / len(kept)


def decode_batch(logits: np.ndarray, labels: list[str]) -> list[tuple[str, float]]:
    """Decode a (N, T, C) batch into one (text, confidence) pair per row."""
    if logits.ndim != 3:
        raise ValueError(f"expected an (N, T, C) array, got shape {logits.shape}")
    return [decode_greedy(row, labels) for row in logits]
