"""The OCR probe: every unavailability path, and that it never raises.

Mirrors test_vlm_registry.py — same probe shape, same reason this file exists:
existing tests stub ``registry.is_available()`` directly and never exercise
the dependency/config checks that decide what that boolean actually is.
"""
import pytest

from extractor.ocr import config, registry
from extractor.ocr.base import OcrUnavailable, UnavailableReason


@pytest.fixture(autouse=True)
def _clean():
    config.reset()
    yield
    config.reset()


def test_disabled_via_no_ocr_flag():
    config.configure(enabled=False)
    assert registry.is_available() is False
    assert registry.unavailable_reason().reason == UnavailableReason.DISABLED


def test_missing_deps_reported(monkeypatch):
    monkeypatch.setattr(registry, "_missing_modules", lambda: ["onnxruntime", "cv2"])

    assert registry.is_available() is False
    failure = registry.unavailable_reason()
    assert failure.reason == UnavailableReason.MISSING_DEPS
    assert "onnxruntime" in failure.detail and "cv2" in failure.detail


def test_resolve_models_failure_downgrades_to_typed_reason(monkeypatch):
    """A missing model file must not surface as a raw exception."""
    monkeypatch.setattr(registry, "_missing_modules", lambda: [])

    def _raise_missing(_model_dir):
        raise OcrUnavailable(UnavailableReason.MISSING_DET_MODEL)

    monkeypatch.setattr("extractor.ocr.paths.resolve_models", _raise_missing)

    assert registry.is_available() is False
    assert registry.unavailable_reason().reason == UnavailableReason.MISSING_DET_MODEL


def test_unexpected_error_downgrades_to_load_failed(monkeypatch):
    monkeypatch.setattr(registry, "_missing_modules", lambda: [])
    monkeypatch.setattr(
        "extractor.ocr.paths.resolve_models",
        lambda _model_dir: (_ for _ in ()).throw(RuntimeError("disk error")),
    )

    assert registry.is_available() is False
    failure = registry.unavailable_reason()
    assert failure.reason == UnavailableReason.LOAD_FAILED
    assert "disk error" in failure.detail


def test_probe_runs_once_and_caches(monkeypatch):
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
    monkeypatch.setattr(
        registry, "_build_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("no weights")),
    )

    with pytest.raises(OcrUnavailable) as exc:
        registry.get_engine()
    assert exc.value.reason == UnavailableReason.LOAD_FAILED


def test_get_engine_returns_the_built_engine(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(registry, "_build_engine", lambda: sentinel)

    assert registry.get_engine() is sentinel
