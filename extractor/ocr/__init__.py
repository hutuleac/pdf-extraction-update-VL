"""Optional OCR support for scanned pages and image files.

Importing this package is always safe: nothing here pulls in onnxruntime,
opencv or numpy at module level. Ask ``registry.is_available()`` before use.
"""
from extractor.ocr.base import (
    OcrEngine,
    OcrLine,
    OcrResult,
    OcrUnavailable,
    UnavailableReason,
)
from extractor.ocr.config import OcrConfig, configure, get_config
from extractor.ocr.registry import get_engine, is_available, unavailable_reason

__all__ = [
    "OcrConfig",
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "OcrUnavailable",
    "UnavailableReason",
    "configure",
    "get_config",
    "get_engine",
    "is_available",
    "unavailable_reason",
]
