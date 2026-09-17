"""render_page's rasterization, and MlxVlmEngine's truncation signal.

Truncation is the only evidence the pipeline has that a page's reading is
incomplete (see vlm/apply.py) — Markdown output gives no other clue, since cut
-off prose looks identical to prose that ended there. `convert()`'s `capped`
return value is that evidence, so its finish_reason/token-count logic is
tested directly here rather than only through higher-level pipeline tests,
which never previously exercised this file at all (0% dedicated coverage).

mlx_vlm's `load`/`generate` are stubbed throughout: loading real weights is
slow and belongs in the (marked-slow) integration suite, not unit tests.
"""
from types import SimpleNamespace

import pymupdf
import pytest

from extractor.vlm.engine import MlxVlmEngine, render_page


def test_render_page_produces_a_png():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello")

    png_bytes = render_page(doc[0], dpi=144)

    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    doc.close()


def test_render_page_higher_dpi_yields_more_bytes():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello world, this is a page of text")

    low = render_page(doc[0], dpi=72)
    high = render_page(doc[0], dpi=300)

    assert len(high) > len(low)
    doc.close()


@pytest.fixture
def stub_load(monkeypatch):
    """Replace mlx_vlm.load with a stub returning sentinel (model, processor)."""
    import mlx_vlm

    model = SimpleNamespace(config=object())
    processor = object()
    calls = []

    def _load(model_name):
        calls.append(model_name)
        return model, processor

    monkeypatch.setattr(mlx_vlm, "load", _load)
    return calls


@pytest.fixture
def stub_generate(monkeypatch):
    """Replace mlx_vlm.generate and apply_chat_template; return a mutable box
    the test fills with the fake result and can inspect the call kwargs from.
    """
    import mlx_vlm
    import mlx_vlm.prompt_utils

    box = {"result": SimpleNamespace(text="stub output"), "calls": []}

    monkeypatch.setattr(
        mlx_vlm.prompt_utils, "apply_chat_template",
        lambda processor, config, prompt, num_images=1: "formatted-prompt",
    )

    def _generate(model, processor, formatted, image_paths, **kwargs):
        box["calls"].append({"image_paths": image_paths, **kwargs})
        return box["result"]

    monkeypatch.setattr(mlx_vlm, "generate", _generate)
    return box


def test_engine_loads_the_named_model(stub_load, stub_generate):
    engine = MlxVlmEngine("some/model-name", "describe this page")

    assert engine.name == "some/model-name"
    assert engine.prompt == "describe this page"
    assert stub_load == ["some/model-name"]


def test_convert_returns_the_generated_text(stub_load, stub_generate):
    stub_generate["result"] = SimpleNamespace(text="recovered prose", finish_reason="stop")
    engine = MlxVlmEngine("m", "p")

    text, capped = engine.convert(b"fake-png-bytes")

    assert text == "recovered prose"
    assert capped is False


def test_convert_falls_back_to_str_when_result_has_no_text_attribute(
    stub_load, stub_generate,
):
    stub_generate["result"] = "plain string result"
    engine = MlxVlmEngine("m", "p")

    text, _ = engine.convert(b"fake-png-bytes")

    assert text == "plain string result"


def test_convert_flags_truncation_from_finish_reason(stub_load, stub_generate):
    stub_generate["result"] = SimpleNamespace(text="cut off mid-sen", finish_reason="length")
    engine = MlxVlmEngine("m", "p")

    _, capped = engine.convert(b"fake-png-bytes", max_tokens=4096)

    assert capped is True


def test_convert_flags_truncation_from_token_count_when_finish_reason_absent(
    stub_load, stub_generate,
):
    """Fallback path for an mlx-vlm build that never sets finish_reason."""
    stub_generate["result"] = SimpleNamespace(text="...", generation_tokens=100)
    engine = MlxVlmEngine("m", "p")

    _, capped = engine.convert(b"fake-png-bytes", max_tokens=100)

    assert capped is True


def test_convert_not_truncated_when_neither_signal_fires(stub_load, stub_generate):
    stub_generate["result"] = SimpleNamespace(
        text="a complete reading", finish_reason="stop", generation_tokens=50,
    )
    engine = MlxVlmEngine("m", "p")

    _, capped = engine.convert(b"fake-png-bytes", max_tokens=4096)

    assert capped is False


def test_convert_forwards_max_tokens_and_repetition_penalty(stub_load, stub_generate):
    engine = MlxVlmEngine("m", "p")

    engine.convert(b"fake-png-bytes", max_tokens=2048, repetition_penalty=1.2)

    call = stub_generate["calls"][0]
    assert call["max_tokens"] == 2048
    assert call["repetition_penalty"] == 1.2


def test_convert_writes_the_page_image_to_a_real_path_during_the_call(
    stub_load, stub_generate, monkeypatch,
):
    """generate() is handed a path mlx-vlm can open at call time — verified by
    checking the file actually existed with the right bytes while `generate`
    ran, since the engine cleans the temp dir up immediately afterwards.
    """
    import mlx_vlm

    seen = {}

    def _generate(model, processor, formatted, image_paths, **kwargs):
        from pathlib import Path

        path = Path(image_paths[0])
        seen["existed"] = path.exists()
        seen["bytes"] = path.read_bytes()
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(mlx_vlm, "generate", _generate)

    engine = MlxVlmEngine("m", "p")
    engine.convert(b"the-png-bytes")

    assert seen["existed"] is True
    assert seen["bytes"] == b"the-png-bytes"
