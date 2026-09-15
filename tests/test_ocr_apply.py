"""Tests for the reader/engine glue in ``extractor.ocr.apply``.

A fake engine stands in for the real models, so these run without onnxruntime
or the .onnx weights.
"""
import pytest

from extractor.ocr import apply, config, registry
from extractor.ocr.base import ICON_PLACEHOLDER, OcrLine, OcrResult


class FakeEngine:
    """An engine that returns a canned result for every image."""

    name = "fake"

    def __init__(self, *results: OcrResult) -> None:
        self._results = list(results)

    def recognize(self, image) -> OcrResult:
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


@pytest.fixture
def use_engine(monkeypatch):
    """Install a fake engine and restore default OCR settings afterwards."""

    def install(*results: OcrResult):
        engine = FakeEngine(*results)
        monkeypatch.setattr(registry, "is_available", lambda: True)
        monkeypatch.setattr(registry, "get_engine", lambda: engine)
        return engine

    yield install
    config.reset()


def _result(*pairs: tuple[str, float]) -> OcrResult:
    """Build a result from (text, confidence) pairs with throwaway boxes."""
    lines = [OcrLine(text, score, (0, i, 1, i + 1)) for i, (text, score) in enumerate(pairs)]
    return OcrResult(lines=lines, engine="fake")


def _by_code(warnings: list[dict], code: str) -> dict | None:
    return next((w for w in warnings if w["code"] == code), None)


def test_page_confidence_weights_every_line_equally(use_engine):
    """A one-line image must not outvote a fifty-line one."""
    use_engine(
        _result(("caption", 0.20)),
        _result(*[(f"body {i}", 1.00) for i in range(9)]),
    )

    block, warnings = apply.ocr_images(["small", "large"])

    # Mean of the 10 lines is 0.92; the old mean-of-image-means gave 0.60.
    assert _by_code(warnings, "OCR_APPLIED")["confidence"] == 0.92
    assert block["confidence"] == 0.92


def test_mixed_confidence_warns_when_a_line_falls_below_the_threshold(use_engine):
    use_engine(_result(
        ("Trends at a Glance", 0.98), ("Active users by month", 0.96), ("2025020020", 0.21),
    ))

    _, warnings = apply.ocr_images(["image"], page_number=4)

    mixed = _by_code(warnings, "OCR_MIXED_CONFIDENCE")
    assert mixed is not None
    assert mixed["page"] == 4
    assert mixed["low_lines"] == 1
    assert mixed["total_lines"] == 3
    assert mixed["lowest"] == 0.21


def test_mixed_confidence_counts_low_lines_across_every_image(use_engine):
    use_engine(
        _result(("clean title", 0.99)),
        _result(("bad axis", 0.10), ("worse axis", 0.05), ("body a", 0.95), ("body b", 0.95)),
    )

    _, warnings = apply.ocr_images(["one", "two"])

    mixed = _by_code(warnings, "OCR_MIXED_CONFIDENCE")
    assert mixed["low_lines"] == 2
    assert mixed["total_lines"] == 5
    assert mixed["lowest"] == 0.05


def test_no_mixed_confidence_warning_when_every_line_is_confident(use_engine):
    use_engine(_result(("clean title", 0.95), ("clean body", 0.88)))

    _, warnings = apply.ocr_images(["image"])

    assert _by_code(warnings, "OCR_MIXED_CONFIDENCE") is None


def test_low_page_confidence_replaces_the_mixed_warning(use_engine):
    """A page that is bad overall needs one warning, not two."""
    use_engine(_result(("garble", 0.20), ("more garble", 0.15)))

    _, warnings = apply.ocr_images(["image"])

    assert _by_code(warnings, "OCR_LOW_CONFIDENCE") is not None
    assert _by_code(warnings, "OCR_MIXED_CONFIDENCE") is None


def test_mixed_confidence_follows_the_configured_threshold(use_engine):
    use_engine(_result(("title", 0.99), ("body", 0.75)))
    config.configure(min_confidence=0.80)

    _, warnings = apply.ocr_images(["image"])

    assert _by_code(warnings, "OCR_MIXED_CONFIDENCE")["low_lines"] == 1


def test_icon_markers_do_not_count_towards_the_page_confidence(use_engine):
    """The marker is ours, not a reading — its score says nothing about text."""
    use_engine(_result(("readable body text", 0.90), (ICON_PLACEHOLDER, 0.30)))

    block, warnings = apply.ocr_images(["image"])

    assert block["confidence"] == 0.90
    assert _by_code(warnings, "OCR_APPLIED")["confidence"] == 0.90


def test_icon_markers_never_trigger_the_mixed_confidence_warning(use_engine):
    use_engine(_result(("readable body text", 0.90), (ICON_PLACEHOLDER, 0.30)))

    _, warnings = apply.ocr_images(["image"])

    assert _by_code(warnings, "OCR_MIXED_CONFIDENCE") is None


def test_icon_markers_stay_in_the_extracted_text(use_engine):
    """Excluded from the statistics, but still shown to the reader."""
    use_engine(_result(("readable body text", 0.90), (ICON_PLACEHOLDER, 0.30)))

    block, _ = apply.ocr_images(["image"])

    assert ICON_PLACEHOLDER in block["content"]


def test_unreadable_short_fragment_is_dropped_from_the_text(use_engine):
    """A sub-threshold 1-3 char read carries no recoverable signal."""
    use_engine(_result(("readable body text", 0.90), ("ω", 0.32)))

    block, _ = apply.ocr_images(["image"])

    assert block["content"] == "readable body text"


def test_confident_short_text_is_kept(use_engine):
    """The confidence gate, not the length, decides — 'OK' is real content."""
    use_engine(_result(("readable body text", 0.90), ("OK", 0.95)))

    block, _ = apply.ocr_images(["image"])

    assert "OK" in block["content"]


def test_long_low_confidence_text_is_kept(use_engine):
    """A garbled sentence still carries structure — flag it, never delete it."""
    use_engine(_result(
        ("readable body text", 0.98), ("more readable text", 0.98), ("and more still", 0.98),
        ("gaibled senlence heie", 0.20),
    ))

    block, warnings = apply.ocr_images(["image"])

    assert "gaibled senlence heie" in block["content"]
    assert _by_code(warnings, "OCR_MIXED_CONFIDENCE")["low_lines"] == 1


def test_discarded_fragments_are_reported_rather_than_silently_dropped(use_engine):
    """A future document must never lose short text without saying so."""
    use_engine(_result(("readable body text", 0.90), ("ω", 0.32), ("e", 0.41)))

    _, warnings = apply.ocr_images(["image"], page_number=7)

    filtered = _by_code(warnings, "OCR_NOISE_FILTERED")
    assert filtered is not None
    assert filtered["page"] == 7
    assert filtered["dropped"] == 2
    assert filtered["sample"] == ["ω", "e"]


def test_no_noise_warning_when_nothing_was_discarded(use_engine):
    use_engine(_result(("readable body text", 0.90)))

    _, warnings = apply.ocr_images(["image"])

    assert _by_code(warnings, "OCR_NOISE_FILTERED") is None


def test_page_left_without_any_readable_text_reports_none(use_engine):
    """Markers and discarded fragments alone are not text."""
    use_engine(_result((ICON_PLACEHOLDER, 0.30), ("ω", 0.32)))

    block, warnings = apply.ocr_images(["image"])

    assert block is None
    assert _by_code(warnings, "OCR_APPLIED")["confidence"] == 0.0


class FlakyEngine:
    """Raises on chosen calls, returns a canned result on the rest."""

    name = "flaky"

    def __init__(self, *outcomes) -> None:
        self._outcomes = list(outcomes)

    def recognize(self, image) -> OcrResult:
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def use_flaky_engine(monkeypatch):
    def install(*outcomes):
        engine = FlakyEngine(*outcomes)
        monkeypatch.setattr(registry, "is_available", lambda: True)
        monkeypatch.setattr(registry, "get_engine", lambda: engine)
        return engine

    yield install
    config.reset()


def test_one_bad_image_does_not_discard_text_from_the_others(use_flaky_engine):
    # AUDIT 3: a single failing image used to return None for the whole page,
    # throwing away every line already recognized from earlier images.
    use_flaky_engine(_result(("good text", 0.95)), RuntimeError("boom"))

    block, warnings = apply.ocr_images(["good", "bad"], page_number=2)

    assert block is not None
    assert "good text" in block["content"]
    failed = _by_code(warnings, "OCR_IMAGE_FAILED")
    assert failed is not None
    assert failed["page"] == 2
    assert failed["images"] == 1
    assert _by_code(warnings, "OCR_FAILED") is None


def test_every_image_failing_reports_ocr_failed(use_flaky_engine):
    """Nothing was read at all, so this keeps OCR_FAILED's existing meaning."""
    use_flaky_engine(RuntimeError("boom"), RuntimeError("boom again"))

    block, warnings = apply.ocr_images(["bad1", "bad2"])

    assert block is None
    assert _by_code(warnings, "OCR_FAILED") is not None
    assert _by_code(warnings, "OCR_IMAGE_FAILED") is None
