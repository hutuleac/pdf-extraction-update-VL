"""Figure description: which pages are sent, and what the NONE gate drops."""
import pymupdf
import pytest

from extractor.vlm import config, describe, registry


@pytest.fixture(autouse=True)
def _clean():
    config.reset()
    yield
    config.reset()


@pytest.fixture
def three_page_pdf(tmp_path):
    doc = pymupdf.open()
    # Pages must differ: the inference cache is keyed on rendered pixels.
    for number in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {number}")
    out = tmp_path / "doc.pdf"
    doc.save(out)
    doc.close()
    return out


class StubEngine:
    def __init__(self, outputs):
        self.name = "stub-describe"
        self.outputs = list(outputs)
        self.calls = 0

    def convert(self, png_bytes, *, max_tokens=512, repetition_penalty=1.0):
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if isinstance(output, Exception):
            raise output
        return output, False


def _arrange(monkeypatch, tmp_path, outputs):
    engine = StubEngine(outputs)
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)
    monkeypatch.setattr(registry, "is_available", lambda: True)
    config.configure(
        enabled=True, describe=True, cache_dir=str(tmp_path / "cache"),
    )
    # configure() resets the probe, which would drop the patched engine.
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)
    return engine


def test_only_figure_class_pages_are_sent(monkeypatch, tmp_path, three_page_pdf):
    engine = _arrange(monkeypatch, tmp_path, ["a chart of X against Y"])
    results, warnings = describe.describe_pages(
        three_page_pdf, ["native-text", "layout-complex", "scanned"], [0, 0, 0],
    )
    assert engine.calls == 1
    assert set(results) == {2}
    assert warnings == [{"code": "VLM_FIGURES_DESCRIBED", "pages": 1}]


def test_none_answer_is_not_published(monkeypatch, tmp_path, three_page_pdf):
    _arrange(monkeypatch, tmp_path, ["NONE", "a map of Romania", "None."])
    results, warnings = describe.describe_pages(
        three_page_pdf, ["mixed", "mixed", "mixed"], [0, 0, 0],
    )
    # Only the page with a real answer survives — a trailing period or a
    # lowercase sentinel is still the model saying "no figures here".
    assert set(results) == {2}
    assert results[2] == "a map of Romania"
    assert warnings == [{"code": "VLM_FIGURES_DESCRIBED", "pages": 1}]


def test_one_page_failing_keeps_the_others(monkeypatch, tmp_path, three_page_pdf):
    _arrange(monkeypatch, tmp_path, [RuntimeError("boom"), "a bar chart", "NONE"])
    results, warnings = describe.describe_pages(
        three_page_pdf, ["mixed", "mixed", "mixed"], [0, 0, 0],
    )
    assert set(results) == {2}
    assert {"code": "VLM_DESCRIBE_FAILED", "pages": 1} in warnings


def test_an_image_page_is_sent_whatever_its_class(monkeypatch, tmp_path, three_page_pdf):
    """The class alone is not "has figures" — a chart page can read as native-text."""
    engine = _arrange(monkeypatch, tmp_path, ["a contoured map of Romania"])
    results, _ = describe.describe_pages(
        three_page_pdf, ["native-text", "native-text", "native-text"], [0, 2, 0],
    )
    assert engine.calls == 1
    assert set(results) == {2}


def test_off_by_default(monkeypatch, tmp_path, three_page_pdf):
    engine = StubEngine(["a chart"])
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)
    config.configure(enabled=True)  # --vlm without --vlm-describe-figures
    assert describe.describe_pages(three_page_pdf, ["mixed"], [0]) == ({}, [])
    assert engine.calls == 0


# --- image files -----------------------------------------------------------

def test_describe_image_needs_no_reading_model(monkeypatch, tmp_path):
    """The registry probe is for the reading model, which this path has none of.

    Consulting it would load granite's weights purely to decide whether a
    different model can run.
    """
    engine = StubEngine(["a supervisor agent branching to four sub-agents"])
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)

    def explode():
        raise AssertionError("the reading model must not be probed here")

    monkeypatch.setattr(registry, "is_available", explode)
    config.configure(enabled=True, describe=True, cache_dir=str(tmp_path))
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)

    text, warnings = describe.describe_image(b"fake png bytes")
    assert text == "a supervisor agent branching to four sub-agents"
    assert warnings == [{"code": "VLM_FIGURES_DESCRIBED", "pages": 1}]


def test_describe_image_publishes_nothing_for_none(monkeypatch, tmp_path):
    engine = StubEngine(["NONE"])
    config.configure(enabled=True, describe=True, cache_dir=str(tmp_path))
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)
    assert describe.describe_image(b"png") == (None, [])


def test_describe_image_off_by_default(monkeypatch):
    engine = StubEngine(["a chart"])
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)
    config.configure(enabled=True)  # --vlm without --vlm-describe-figures
    assert describe.describe_image(b"png") == (None, [])
    assert engine.calls == 0


# --- _get_engine's own probe, never exercised by the tests above -----------
# (every test so far monkeypatches `describe._get_engine` wholesale, the same
# gap test_vlm_registry.py existed to close for the reading model's probe)

def test_get_engine_builds_and_caches_the_describing_engine(monkeypatch):
    calls = []

    class _Engine:
        def __init__(self, model_name, prompt):
            calls.append((model_name, prompt))

    monkeypatch.setattr("extractor.vlm.engine.MlxVlmEngine", _Engine)

    first = describe._get_engine()
    second = describe._get_engine()

    assert first is second
    assert len(calls) == 1
    assert calls[0][0] == config.get_config().describe_model


def test_get_engine_downgrades_a_load_failure_to_vlm_unavailable(monkeypatch):
    from extractor.vlm.base import UnavailableReason, VlmUnavailable

    monkeypatch.setattr(
        "extractor.vlm.engine.MlxVlmEngine",
        lambda *a: (_ for _ in ()).throw(RuntimeError("weights corrupt")),
    )

    with pytest.raises(VlmUnavailable) as exc:
        describe._get_engine()
    assert exc.value.reason == UnavailableReason.MODEL_LOAD_FAILED
    assert "weights corrupt" in exc.value.detail

    # And the failure is cached too — a second call must not reload.
    with pytest.raises(VlmUnavailable):
        describe._get_engine()


# --- describe_pages: the early-outs and the unavailable path ----------------

def test_describe_pages_returns_nothing_when_registry_unavailable(
    monkeypatch, three_page_pdf,
):
    engine = StubEngine(["a chart"])
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)
    monkeypatch.setattr(registry, "is_available", lambda: False)
    config.configure(enabled=True, describe=True)

    results, warnings = describe.describe_pages(
        three_page_pdf, ["mixed", "mixed", "mixed"], [0, 0, 0],
    )

    # The reading path already reports VLM_UNAVAILABLE; repeating it here
    # would print the same failure twice.
    assert (results, warnings) == ({}, [])
    assert engine.calls == 0


def test_describe_pages_returns_nothing_when_no_page_qualifies(
    monkeypatch, tmp_path, three_page_pdf,
):
    engine = _arrange(monkeypatch, tmp_path, ["a chart"])
    results, warnings = describe.describe_pages(
        three_page_pdf, ["native-text", "native-text", "native-text"], [0, 0, 0],
    )
    assert (results, warnings) == ({}, [])
    assert engine.calls == 0


def test_describe_pages_reports_unavailable_engine(monkeypatch, three_page_pdf):
    from extractor.vlm.base import UnavailableReason, VlmUnavailable

    monkeypatch.setattr(registry, "is_available", lambda: True)
    monkeypatch.setattr(
        describe, "_get_engine",
        lambda: (_ for _ in ()).throw(VlmUnavailable(UnavailableReason.MODEL_LOAD_FAILED)),
    )
    config.configure(enabled=True, describe=True)

    results, warnings = describe.describe_pages(
        three_page_pdf, ["mixed", "mixed", "mixed"], [0, 0, 0],
    )

    assert results == {}
    assert warnings == [{
        "code": "VLM_DESCRIBE_UNAVAILABLE",
        "detail": UnavailableReason.MODEL_LOAD_FAILED.describe(),
        "pages": 3,
    }]


def test_describe_pages_reports_truncation(monkeypatch, tmp_path, three_page_pdf):
    """A description cut off is still kept (unlike a formula), but flagged —
    the reader cannot otherwise tell prose that stopped from prose that ended.
    """
    class _CappedEngine:
        name = "stub-capped"

        def convert(self, png_bytes, *, max_tokens=512, repetition_penalty=1.0):
            return "a chart that got cut off mid", True

    monkeypatch.setattr(describe, "_get_engine", lambda: _CappedEngine())
    monkeypatch.setattr(registry, "is_available", lambda: True)
    config.configure(enabled=True, describe=True, cache_dir=str(tmp_path / "cache"))
    monkeypatch.setattr(describe, "_get_engine", lambda: _CappedEngine())

    results, warnings = describe.describe_pages(
        three_page_pdf, ["mixed", "mixed", "mixed"], [0, 0, 0],
    )

    assert len(results) == 3
    assert {"code": "VLM_DESCRIBE_TRUNCATED", "pages": 3} in warnings


# --- describe_image's failure paths, untested above -------------------------

def test_describe_image_reports_unavailable_engine(monkeypatch):
    from extractor.vlm.base import UnavailableReason, VlmUnavailable

    monkeypatch.setattr(
        describe, "_get_engine",
        lambda: (_ for _ in ()).throw(VlmUnavailable(UnavailableReason.MISSING_DEPS, "mlx")),
    )
    config.configure(enabled=True, describe=True)

    text, warnings = describe.describe_image(b"png")

    assert text is None
    assert warnings == [{
        "code": "VLM_DESCRIBE_UNAVAILABLE",
        "detail": UnavailableReason.MISSING_DEPS.describe(),
        "pages": 1,
    }]


def test_describe_image_reports_inference_failure(monkeypatch, tmp_path):
    engine = StubEngine([RuntimeError("model crashed")])
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)
    config.configure(enabled=True, describe=True, cache_dir=str(tmp_path))
    monkeypatch.setattr(describe, "_get_engine", lambda: engine)

    text, warnings = describe.describe_image(b"png")

    assert text is None
    assert warnings == [{"code": "VLM_DESCRIBE_FAILED", "pages": 1}]


def test_describe_model_does_not_share_the_reading_cache(tmp_path):
    """Both passes see identical pixels and answer different questions."""
    from extractor.vlm.apply import _cache_path

    config.configure(cache_dir=str(tmp_path))
    png = b"identical pixels"
    reading = _cache_path(png, "granite-docling", 4096, 1.05, "convert this page")
    describing = _cache_path(png, "Qwen3-VL-8B-Instruct-4bit", 512, 1.0, "describe")
    assert reading != describing
    # The prompt is the question. Editing one to fix bad output has to re-read
    # the page, or the fix is invisible behind the entry it was meant to replace.
    assert describing != _cache_path(
        png, "Qwen3-VL-8B-Instruct-4bit", 512, 1.0, "describe, but differently",
    )
