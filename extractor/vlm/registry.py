"""Is the visual model runnable here, and if not, exactly why.

The probe runs once per process and never raises: a missing extra, a non-Mac
host or a failed load all downgrade to a typed reason the run reports.
"""
from __future__ import annotations

import logging
import platform

from extractor.vlm.base import UnavailableReason, VlmUnavailable
from extractor.vlm.config import get_config
from extractor.vlm.models import for_model

logger = logging.getLogger(__name__)

REQUIRED_MODULES = ("mlx", "mlx_vlm")

_engine = None
_failure: VlmUnavailable | None = None
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


def host_failure() -> VlmUnavailable | None:
    """Why this host cannot run any mlx model, or None. No weights are loaded."""
    # mlx is Apple-Silicon only. Checked before the import so a Windows box
    # gets the useful reason rather than "module not found".
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return VlmUnavailable(
            UnavailableReason.UNSUPPORTED_PLATFORM,
            f"{platform.system()}/{platform.machine()}",
        )
    missing = _missing_modules()
    if missing:
        return VlmUnavailable(UnavailableReason.MISSING_DEPS, ", ".join(missing))
    return None


def _build_engine():
    """Construct the engine or raise VlmUnavailable with a typed reason."""
    config = get_config()
    if not config.enabled:
        raise VlmUnavailable(UnavailableReason.DISABLED)

    failure = host_failure()
    if failure:
        raise failure

    # Refuses an unreadable model name before the weights are loaded, so a
    # typo costs a clear reason rather than a minute and an empty document.
    spec = for_model(config.model)

    from extractor.vlm.engine import MlxVlmEngine

    return MlxVlmEngine(config.model, spec.prompt)


def _probe() -> None:
    """Run the one-time availability probe, caching engine or failure."""
    global _engine, _failure, _probed
    if _probed:
        return
    _probed = True
    try:
        _engine = _build_engine()
        logger.debug("Visual model ready: %s", _engine.name)
    except VlmUnavailable as exc:
        _failure = exc
        logger.debug("Visual model unavailable: %s", exc)
    except Exception as exc:  # noqa: BLE001 - never let a probe break extraction
        _failure = VlmUnavailable(UnavailableReason.MODEL_LOAD_FAILED, str(exc))
        logger.debug("Visual model probe failed: %s", exc)


def is_available() -> bool:
    """Return True when the visual model can run in this process."""
    _probe()
    return _engine is not None


def unavailable_reason() -> VlmUnavailable | None:
    """Return the typed failure, or None when the model is available."""
    _probe()
    return _failure


def get_engine():
    """Return the ready engine, or raise VlmUnavailable explaining why not."""
    _probe()
    if _engine is None:
        raise _failure or VlmUnavailable(UnavailableReason.MODEL_LOAD_FAILED)
    return _engine
