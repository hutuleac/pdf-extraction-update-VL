"""The visual-model probe: every unavailability path, and that it never raises.

This exact module caused two production incidents (missing torchvision dep
going unreported, and an order-of-operations bug hiding the warning), yet had
no dedicated tests — the pipeline tests only ever stubbed
``registry.is_available()`` directly, so a regression in the probe's own
dependency/platform checks would pass silently. See CHANGELOG for the incident.
"""
import pytest

from extractor.vlm import config, registry
from extractor.vlm.base import UnavailableReason, VlmUnavailable


@pytest.fixture(autouse=True)
def _clean():
    config.reset()
    yield
    config.reset()


def test_disabled_by_default():
    assert registry.is_available() is False
    assert registry.unavailable_reason().reason == UnavailableReason.DISABLED


def test_unsupported_platform_reported_before_missing_deps(monkeypatch):
    """A non-Mac host must get UNSUPPORTED_PLATFORM, not a raw import error.

    Checked in this order deliberately (see registry.py) so a Windows/Linux
    user is told the real reason rather than "module not found".
    """
    config.configure(enabled=True)
    monkeypatch.setattr(registry.platform, "system", lambda: "Windows")
    monkeypatch.setattr(registry.platform, "machine", lambda: "AMD64")

    assert registry.is_available() is False
    failure = registry.unavailable_reason()
    assert failure.reason == UnavailableReason.UNSUPPORTED_PLATFORM
    assert "Windows" in failure.detail


def test_missing_deps_reported_on_supported_platform(monkeypatch):
    """Apple Silicon with mlx/mlx_vlm not importable -> MISSING_DEPS, named."""
    config.configure(enabled=True)
    monkeypatch.setattr(registry.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(registry.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(registry, "_missing_modules", lambda: ["mlx", "mlx_vlm"])

    assert registry.is_available() is False
    failure = registry.unavailable_reason()
    assert failure.reason == UnavailableReason.MISSING_DEPS
    assert "mlx" in failure.detail and "mlx_vlm" in failure.detail


def test_unknown_model_name_reported_before_loading_weights(monkeypatch):
    """A typo in --vlm-model must fail fast with UNKNOWN_MODEL, not load weights."""
    config.configure(enabled=True, model="not-a-real-model")
    monkeypatch.setattr(registry.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(registry.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(registry, "_missing_modules", lambda: [])

    assert registry.is_available() is False
    assert registry.unavailable_reason().reason == UnavailableReason.UNKNOWN_MODEL


def test_unexpected_error_downgrades_to_model_load_failed(monkeypatch):
    """Any surprise during engine construction must become a typed failure,
    never an exception the caller has to catch — that is the whole contract
    of a 'probe that never raises'.
    """
    config.configure(enabled=True)

    def _boom():
        raise RuntimeError("weights corrupt")

    monkeypatch.setattr(registry, "_build_engine", _boom)

    assert registry.is_available() is False
    failure = registry.unavailable_reason()
    assert failure.reason == UnavailableReason.MODEL_LOAD_FAILED
    assert "weights corrupt" in failure.detail


def test_probe_runs_once_and_caches(monkeypatch):
    """Repeated calls must not re-probe — model loading is too expensive."""
    config.configure(enabled=True)
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(registry, "_build_engine", _boom)

    registry.is_available()
    registry.is_available()
    registry.unavailable_reason()

    assert len(calls) == 1


def test_reset_forces_a_fresh_probe(monkeypatch):
    config.configure(enabled=True)
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(registry, "_build_engine", _boom)

    registry.is_available()
    registry.reset()
    registry.is_available()

    assert len(calls) == 2


def test_get_engine_raises_the_recorded_failure(monkeypatch):
    config.configure(enabled=True)
    monkeypatch.setattr(
        registry, "_build_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("no weights")),
    )

    with pytest.raises(VlmUnavailable) as exc:
        registry.get_engine()
    assert exc.value.reason == UnavailableReason.MODEL_LOAD_FAILED


def test_get_engine_returns_the_built_engine(monkeypatch):
    config.configure(enabled=True)
    sentinel = object()
    monkeypatch.setattr(registry, "_build_engine", lambda: sentinel)

    assert registry.get_engine() is sentinel
