"""Engine discovery: is OCR runnable here, and if not, exactly why.

The probe runs once per process. Every failure path produces a typed reason
rather than a bare "no OCR", because the user has to be told what to fix.
"""
from __future__ import annotations

import logging

from extractor.ocr.base import OcrUnavailable, UnavailableReason
from extractor.ocr.config import get_config

logger = logging.getLogger(__name__)

REQUIRED_MODULES = ("onnxruntime", "cv2", "numpy", "pyclipper")

_engine = None
_failure: OcrUnavailable | None = None
_probed = False


def reset() -> None:
    """Forget the cached probe — used when configuration changes, and in tests."""
    global _engine, _failure, _probed
    _engine = None
    _failure = None
    _probed = False


def _missing_modules() -> list[str]:
    """Return the required third-party modules that cannot be imported."""
    import importlib.util

    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def _build_engine():
    """Construct the ONNX engine or raise OcrUnavailable with a typed reason."""
    config = get_config()
    if not config.enabled:
        raise OcrUnavailable(UnavailableReason.DISABLED)

    missing = _missing_modules()
    if missing:
        raise OcrUnavailable(UnavailableReason.MISSING_DEPS, ", ".join(missing))

    from extractor.ocr.onnx_engine import OnnxOcrEngine
    from extractor.ocr.paths import resolve_models

    det_path, rec_path, dict_path = resolve_models(config.model_dir)
    return OnnxOcrEngine(det_path, rec_path, dict_path)


def _probe() -> None:
    """Run the one-time availability probe, caching engine or failure."""
    global _engine, _failure, _probed
    if _probed:
        return
    _probed = True
    try:
        _engine = _build_engine()
        logger.debug("OCR engine ready: %s", _engine.name)
    except OcrUnavailable as exc:
        _failure = exc
        logger.debug("OCR unavailable: %s", exc)
    except Exception as exc:  # noqa: BLE001 - never let a probe break extraction
        _failure = OcrUnavailable(UnavailableReason.LOAD_FAILED, str(exc))
        logger.debug("OCR probe failed: %s", exc)


def is_available() -> bool:
    """Return True when OCR can run in this process."""
    _probe()
    return _engine is not None


def unavailable_reason() -> OcrUnavailable | None:
    """Return the typed failure, or None when OCR is available."""
    _probe()
    return _failure


def get_engine():
    """Return the ready OCR engine, or raise OcrUnavailable explaining why not."""
    _probe()
    if _engine is None:
        raise _failure or OcrUnavailable(UnavailableReason.LOAD_FAILED)
    return _engine
