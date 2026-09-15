# Granite-Docling Visual-Semantic Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover formulas and structure from the ~38 PDF pages per document whose native text is damaged or absent, by routing whole rendered pages through a local vision-language model and merging only validated output.

**Architecture:** A new `extractor/vlm/` layer mirroring the existing `extractor/ocr/` one exactly — module-level config, a cached availability probe with typed failure reasons, an engine, and an `apply.py` glue module that readers call. `pdf_reader` gains one call to `vlm_pages()`, the same shape as its existing `_ocr_pages()` call. The layer is optional: without `mlx-vlm` installed the probe fails, a structured warning is emitted, and the pipeline produces exactly today's output.

**Tech Stack:** Python 3.11+, `mlx-vlm` (optional extra, Apple Silicon), `ibm-granite/granite-docling-258M-mlx`, PyMuPDF (already a dependency, used for page rendering).

**Spec:** `docs/spike-2026-09-05-granite-docling.md` — the measured evaluation this plan implements. Read it first; every threshold and rejection rule below traces to a measurement in it.

## Global Constraints

- **100% local.** No hosted APIs, no network calls at run time. The model is downloaded once by the user, like the OCR weights.
- **Native text and native tables stay authoritative.** VLM output never silently overwrites a healthy native block. The one exception is a `garbled` page, where the pipeline *already* replaces untrusted native text with OCR (`pdf_reader.py:410`); VLM replacement there follows that established precedent.
- **Optional dependency, graceful absence.** Missing `mlx-vlm`, missing model, or a non-Apple-Silicon host must downgrade to a warning, never a crash. Follow `extractor/ocr/registry.py` exactly.
- **Windows keeps working.** The VLM path is macOS/Apple-Silicon-only for now (`onnx-community/granite-docling-258M-ONNX` is an untested future option — see spec finding 6). On any other platform the probe returns `UNSUPPORTED_PLATFORM` and the run proceeds without it.
- **Off by default.** Enabled with `--vlm`. A default-on slow path would be a surprise.
- **Every quality issue is a structured warning** on `document.warnings`, with wording in `extractor/warning_text.py`. Four new codes only: `VLM_APPLIED`, `VLM_OUTPUT_REJECTED`, `VLM_UNAVAILABLE`, `FORMULA_REVIEW_REQUIRED`.
- **Cache every inference.** Key includes the rendered page bytes, model name, DPI and max_tokens. At ~14 s/page, re-running to change a merge rule must not re-infer.
- Line length 100 (`ruff`), `from __future__ import annotations` at the top of new modules, docstrings that say *why* — match the surrounding code.

## File Structure

**Create:**
- `extractor/vlm/__init__.py` — empty, package marker.
- `extractor/vlm/config.py` — `VlmConfig` + `get_config`/`configure`/`reset`. Mirrors `ocr/config.py`.
- `extractor/vlm/base.py` — `VlmUnavailable`, `UnavailableReason`, `VlmPage` result type. Must stay importable with no VLM dependencies installed.
- `extractor/vlm/registry.py` — one-time cached availability probe. Mirrors `ocr/registry.py`.
- `extractor/vlm/doctag.py` — parse Granite's `<doctag>` output into internal-model blocks; validate it. **Pure string handling, no model needed** — this is where most of the test coverage lives.
- `extractor/vlm/engine.py` — render a page and run the model.
- `extractor/vlm/apply.py` — routing, disk cache, warnings. The only module `pdf_reader` imports.

**Modify:**
- `extractor/model.py` — add `make_vlm_text_block`.
- `extractor/pdf_reader.py:329-455` — call `vlm_pages()`, merge its blocks.
- `extractor/markdown_writer.py:84-92` — render the `source == "vlm"` callout.
- `extractor/warning_text.py` — wording for the four new codes.
- `main.py` — `--vlm`, `--vlm-dpi`, `--vlm-max-tokens`, `--vlm-model` flags.
- `pyproject.toml` — `vlm` optional extra.
- `README.md`, `CHANGELOG.md`.

**Test:**
- `tests/test_vlm_doctag.py`, `tests/test_vlm_apply.py`, `tests/test_vlm_registry.py`, `tests/golden/test_vlm_pages.py`.

---

### Task 1: Config and availability probe

Nothing here imports `mlx_vlm`. The goal is that `import extractor.vlm.apply` is safe on a Windows box with no VLM dependencies at all.

**Files:**
- Create: `extractor/vlm/__init__.py`, `extractor/vlm/base.py`, `extractor/vlm/config.py`, `extractor/vlm/registry.py`
- Modify: `pyproject.toml`
- Test: `tests/test_vlm_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `VlmConfig(enabled: bool, model: str, dpi: int, max_tokens: int, cache_dir: str | None)`; `config.get_config() -> VlmConfig`; `config.configure(**overrides) -> VlmConfig`; `config.reset() -> VlmConfig`; `registry.is_available() -> bool`; `registry.unavailable_reason() -> VlmUnavailable | None`; `registry.get_engine()`; `registry.reset() -> None`; `base.VlmUnavailable(reason, detail="")` with `.reason` and `.detail`; `base.UnavailableReason` StrEnum with members `DISABLED`, `MISSING_DEPS`, `UNSUPPORTED_PLATFORM`, `MODEL_LOAD_FAILED` and a `.describe()` method; `base.VlmPage(page: int, raw: str, truncated: bool)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_registry.py
"""The probe must be honest about why VLM is unavailable, and must never raise."""
import sys

import pytest

from extractor.vlm import config, registry
from extractor.vlm.base import UnavailableReason, VlmUnavailable


@pytest.fixture(autouse=True)
def _clean_config():
    config.reset()
    yield
    config.reset()


def test_disabled_by_default():
    assert config.get_config().enabled is False


def test_disabled_reports_disabled_reason():
    assert registry.is_available() is False
    reason = registry.unavailable_reason()
    assert isinstance(reason, VlmUnavailable)
    assert reason.reason is UnavailableReason.DISABLED


def test_missing_deps_reported_when_enabled(monkeypatch):
    config.configure(enabled=True)
    monkeypatch.setattr(registry, "_missing_modules", lambda: ["mlx_vlm"])
    monkeypatch.setattr(registry, "_platform_supported", lambda: True)

    assert registry.is_available() is False
    reason = registry.unavailable_reason()
    assert reason.reason is UnavailableReason.MISSING_DEPS
    assert "mlx_vlm" in reason.detail


def test_unsupported_platform_reported_before_deps(monkeypatch):
    """A Windows box should be told it is the wrong platform, not to pip install."""
    config.configure(enabled=True)
    monkeypatch.setattr(registry, "_platform_supported", lambda: False)

    assert registry.is_available() is False
    assert registry.unavailable_reason().reason is UnavailableReason.UNSUPPORTED_PLATFORM


def test_probe_never_raises(monkeypatch):
    """An engine that explodes on construction must still downgrade to a reason."""
    config.configure(enabled=True)
    monkeypatch.setattr(registry, "_platform_supported", lambda: True)
    monkeypatch.setattr(registry, "_missing_modules", lambda: [])

    def boom():
        raise RuntimeError("model file corrupt")

    monkeypatch.setattr(registry, "_load_engine", boom)

    assert registry.is_available() is False
    assert registry.unavailable_reason().reason is UnavailableReason.MODEL_LOAD_FAILED


def test_configure_resets_the_cached_probe(monkeypatch):
    config.configure(enabled=True)
    monkeypatch.setattr(registry, "_platform_supported", lambda: False)
    assert registry.is_available() is False

    monkeypatch.setattr(registry, "_platform_supported", lambda: True)
    monkeypatch.setattr(registry, "_missing_modules", lambda: [])
    monkeypatch.setattr(registry, "_load_engine", lambda: object())
    config.configure(enabled=True)  # must drop the cached failure

    assert registry.is_available() is True


def test_base_imports_without_vlm_dependencies():
    """base/config/registry must import on a machine with no mlx at all."""
    assert "mlx_vlm" not in sys.modules
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_vlm_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extractor.vlm'`

- [ ] **Step 3: Write the implementation**

```python
# extractor/vlm/__init__.py
```

(empty file)

```python
# extractor/vlm/base.py
"""VLM value types and failure reasons — no heavy imports here.

Mirrors ``extractor/ocr/base.py``: this module must stay importable on a
machine with no VLM dependencies and on a platform the engine cannot run on,
so the rest of the pipeline can talk about VLM results without paying for
mlx or torch.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UnavailableReason(StrEnum):
    """Why the visual fallback cannot run, in words a user can act on."""

    DISABLED = "disabled"
    MISSING_DEPS = "missing-deps"
    UNSUPPORTED_PLATFORM = "unsupported-platform"
    MODEL_LOAD_FAILED = "model-load-failed"

    def describe(self) -> str:
        """Return a plain-language explanation of this reason."""
        return _REASON_TEXT[self]


_REASON_TEXT: dict[UnavailableReason, str] = {
    UnavailableReason.DISABLED: "the visual fallback was not enabled — pass --vlm",
    UnavailableReason.MISSING_DEPS: (
        'visual fallback dependencies are not installed — run: pip install -e ".[vlm]"'
    ),
    # Named separately from MISSING_DEPS because telling a Windows user to pip
    # install a package that cannot work there sends them down a dead end.
    UnavailableReason.UNSUPPORTED_PLATFORM: (
        "the visual fallback needs Apple Silicon (mlx); this machine cannot run it"
    ),
    UnavailableReason.MODEL_LOAD_FAILED: "the visual model could not be loaded",
}


class VlmUnavailable(Exception):
    """Raised when the visual fallback is requested but cannot be provided."""

    def __init__(self, reason: UnavailableReason, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        message = reason.describe()
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


@dataclass(frozen=True)
class VlmPage:
    """One page's raw model output.

    *truncated* records that generation hit the token cap without closing
    ``</doctag>`` — measured on 10 of 35 garbled pages in the spike, and the
    single strongest signal that the output is a degenerate repetition loop.
    """

    page: int
    raw: str
    truncated: bool
```

```python
# extractor/vlm/config.py
"""Run-wide visual-fallback settings, set once from the CLI.

Same pattern and same reason as ``extractor/ocr/config.py``: the reader entry
points take only a path, so options travel through this module-level holder
rather than through every signature.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

DEFAULT_MODEL = "ibm-granite/granite-docling-258M-mlx"

# 250 DPI is what the spike measured; the model's own preprocessing downsamples
# from here, so more costs render time without adding recognition detail.
DEFAULT_DPI = 250

# The spike ran at 4096 and 10 of 35 garbled pages hit the cap mid-formula.
# 8192 buys headroom for the dense derivation pages; a page that still fails to
# terminate is a repetition loop, not a long page, and gets rejected.
DEFAULT_MAX_TOKENS = 8192


@dataclass(frozen=True)
class VlmConfig:
    """User-facing visual-fallback options for one run."""

    # Off by default: this path costs ~14 s/page, so it must be asked for.
    enabled: bool = False
    model: str = DEFAULT_MODEL
    dpi: int = DEFAULT_DPI
    max_tokens: int = DEFAULT_MAX_TOKENS
    cache_dir: str | None = None


_config = VlmConfig()


def get_config() -> VlmConfig:
    """Return the settings in force for this run."""
    return _config


def configure(**overrides) -> VlmConfig:
    """Replace the current settings and reset any cached engine probe."""
    global _config
    _config = replace(_config, **overrides)
    _reset_probe()
    return _config


def reset() -> VlmConfig:
    """Restore the default settings — used between test cases."""
    global _config
    _config = VlmConfig()
    _reset_probe()
    return _config


def _reset_probe() -> None:
    """Drop the cached engine so the next call re-probes with new settings."""
    from extractor.vlm import registry

    registry.reset()
```

```python
# extractor/vlm/registry.py
"""Engine discovery: can the visual fallback run here, and if not, exactly why.

The probe runs once per process. Every failure path produces a typed reason
rather than a bare "no VLM", because the user has to be told what to fix — and
on a non-Apple-Silicon machine what to fix is *not* a missing package.
"""
from __future__ import annotations

import logging
import platform

from extractor.vlm.base import UnavailableReason, VlmUnavailable
from extractor.vlm.config import get_config

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


def _platform_supported() -> bool:
    """mlx runs on Apple Silicon only."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _missing_modules() -> list[str]:
    """Return the required third-party modules that cannot be imported."""
    import importlib.util

    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def _load_engine():
    """Import and construct the engine. Split out so tests can replace it."""
    from extractor.vlm.engine import GraniteDoclingEngine

    return GraniteDoclingEngine(get_config().model)


def _build_engine():
    """Construct the engine or raise VlmUnavailable with a typed reason."""
    if not get_config().enabled:
        raise VlmUnavailable(UnavailableReason.DISABLED)
    # Platform is checked before dependencies on purpose: on Windows the
    # packages are not installable, so reporting them missing is a dead end.
    if not _platform_supported():
        raise VlmUnavailable(
            UnavailableReason.UNSUPPORTED_PLATFORM,
            f"{platform.system()}/{platform.machine()}",
        )
    missing = _missing_modules()
    if missing:
        raise VlmUnavailable(UnavailableReason.MISSING_DEPS, ", ".join(missing))
    return _load_engine()


def _probe() -> None:
    """Run the one-time availability probe, caching engine or failure."""
    global _engine, _failure, _probed
    if _probed:
        return
    _probed = True
    try:
        _engine = _build_engine()
    except VlmUnavailable as exc:
        _failure = exc
        logger.debug("Visual fallback unavailable: %s", exc)
    except Exception as exc:  # noqa: BLE001 - never let a probe break extraction
        _failure = VlmUnavailable(UnavailableReason.MODEL_LOAD_FAILED, str(exc))
        logger.debug("Visual fallback probe failed: %s", exc)


def is_available() -> bool:
    """Return True when the visual fallback can run in this process."""
    _probe()
    return _engine is not None


def unavailable_reason() -> VlmUnavailable | None:
    """Return the typed failure, or None when the fallback is available."""
    _probe()
    return _failure


def get_engine():
    """Return the ready engine, or raise VlmUnavailable explaining why not."""
    _probe()
    if _engine is None:
        raise _failure or VlmUnavailable(UnavailableReason.MODEL_LOAD_FAILED)
    return _engine
```

Add to `pyproject.toml`, directly after the `ocr-hf` extra:

```toml
# Apple Silicon only — the pipeline runs unchanged without it.
vlm = ["mlx-vlm>=0.6,<1", "torchvision>=0.20"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vlm_registry.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add extractor/vlm/ tests/test_vlm_registry.py pyproject.toml
git commit -m "Add visual-fallback config and availability probe"
```

---

### Task 2: Parse and validate Granite's doctag output

The highest-value task and the only one testable without the model. Every threshold traces to the spike's measurements.

**Files:**
- Create: `extractor/vlm/doctag.py`
- Test: `tests/test_vlm_doctag.py`

**Interfaces:**
- Consumes: `extractor.model.make_text_block`, `make_table_block` (existing).
- Produces:
  - `strip_locations(raw: str) -> str`
  - `is_truncated(raw: str) -> bool`
  - `formula_is_balanced(latex: str) -> bool`
  - `parse(raw: str) -> ParsedPage`
  - `ParsedPage` dataclass with fields `text: str`, `tables: list[list[list[str]]]`, `formula_count: int`, `rejected_formulas: int`, `has_picture: bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_doctag.py
"""Parsing and validating granite-docling's doctag output.

The samples below are real output captured in the 2026-09-05 spike — see
docs/spike-2026-09-05-granite-docling.md.
"""
from extractor.vlm.doctag import (
    formula_is_balanced,
    is_truncated,
    parse,
    strip_locations,
)

PAGE_046 = (
    "<doctag><page_header><loc_49><loc_21><loc_255><loc_28>Stanciu A. • Lungu I."
    "</page_header>\n"
    "<section_header_level_1><loc_67><loc_36><loc_253><loc_44>"
    "Pamanturi cu continut de materii organice</section_header_level_1>\n"
    "<unordered_list><list_item><loc_66><loc_47><loc_431><loc_77>"
    "• Malurile - pamanturi cu un continut de materii organice sub 5%."
    "</list_item>\n"
    "<list_item><loc_66><loc_79><loc_431><loc_93>"
    "• Namolurile - pamanturi asemanatoare malurilor.</list_item>\n"
    "</unordered_list>\n"
    "<picture><loc_222><loc_50><loc_426><loc_245></picture>\n"
    "<page_footer><loc_449><loc_470><loc_456><loc_477>5</page_footer>\n"
    "</doctag>"
)

PAGE_073_FORMULA = (
    "<doctag><text><loc_10><loc_10><loc_20><loc_20>unde</text>\n"
    "<formula>w = \\frac { M _ { w } } { M _ { s } } 1 0 0 = 8 , 8 2 \\%</formula>\n"
    "</doctag>"
)

PAGE_058_TABLE = (
    "<doctag><otsl><ched>Caracterizare<ched>Necoeziv<ched>Coeziv<nl>"
    "<fcel>Uscat<fcel>0 - 0,40<fcel>0 - 0,50<nl>"
    "<fcel>Umed<fcel>0,40 - 0,80<fcel>0,50 - 0,80<nl></otsl></doctag>"
)

# Page 176: one Egorov formula degenerated into a repeating fragment that ate
# the whole token budget. The tag is never closed and neither is </doctag>.
PAGE_176_TRUNCATED = (
    "<doctag><section_header_level_1><loc_1><loc_2><loc_3><loc_4>Metoda Egorov"
    "</section_header_level_1>\n"
    "<formula>\\left [ \\frac { z } { b } \\right ] \\left [ \\frac { z } { b }"
)


def test_strip_locations_removes_coordinate_tags():
    assert "<loc_" not in strip_locations(PAGE_046)
    assert "Stanciu A." in strip_locations(PAGE_046)


def test_is_truncated_false_for_complete_output():
    assert is_truncated(PAGE_046) is False


def test_is_truncated_true_when_doctag_never_closes():
    assert is_truncated(PAGE_176_TRUNCATED) is True


def test_is_truncated_true_when_a_tag_is_left_open():
    """Closing </doctag> is not enough if a formula inside it never closed."""
    assert is_truncated("<doctag><formula>x = 1</doctag>") is True


def test_formula_balance_accepts_matched_delimiters():
    assert formula_is_balanced(r"\left( \frac{a}{b} \right)") is True


def test_formula_balance_rejects_unmatched_delimiters():
    """6 of 127 formulas in the spike had \\left without \\right."""
    assert formula_is_balanced(r"\left[ \frac{z}{b}") is False


def test_formula_balance_ignores_formulas_without_delimiters():
    assert formula_is_balanced("a = b + c") is True


def test_parse_joins_prose_in_reading_order():
    result = parse(PAGE_046)

    assert "Pamanturi cu continut de materii organice" in result.text
    assert "• Malurile" in result.text
    assert "• Namolurile" in result.text
    # Order is the order the model emitted, which is reading order.
    assert result.text.index("Malurile") < result.text.index("Namolurile")


def test_parse_drops_page_headers_and_footers():
    """The native pipeline already detects and separates these; taking the
    model's copy too would duplicate them into the prose."""
    result = parse(PAGE_046)

    assert "Stanciu A." not in result.text
    assert result.text.strip() != ""


def test_parse_records_a_picture_without_inventing_a_caption():
    result = parse(PAGE_046)

    assert result.has_picture is True
    assert "picture" not in result.text.lower()


def test_parse_wraps_formulas_as_display_math():
    result = parse(PAGE_073_FORMULA)

    assert result.formula_count == 1
    assert result.rejected_formulas == 0
    assert "$$" in result.text
    assert r"\frac { M _ { w } } { M _ { s } }" in result.text
    assert "unde" in result.text


def test_parse_drops_an_unbalanced_formula_and_counts_it():
    """An unbalanced formula is never emitted as math — a wrong equation that
    renders is worse than a missing one, because nothing flags it."""
    raw = "<doctag><text><loc_1><loc_1><loc_1><loc_1>x</text>" \
          "<formula>\\left[ \\frac{z}{b}</formula></doctag>"

    result = parse(raw)

    assert result.formula_count == 0
    assert result.rejected_formulas == 1
    assert "$$" not in result.text


def test_parse_extracts_an_otsl_table_as_rows():
    result = parse(PAGE_058_TABLE)

    assert result.tables == [
        ["Caracterizare", "Necoeziv", "Coeziv"],
        ["Uscat", "0 - 0,40", "0 - 0,50"],
        ["Umed", "0,40 - 0,80", "0,50 - 0,80"],
    ]
    # Table content must not also appear in the prose, or it lands twice.
    assert "Uscat" not in result.text


def test_parse_handles_span_and_empty_cells():
    raw = ("<doctag><otsl><ched>A<ched>B<lcel><nl>"
           "<ecel><fcel>2<fcel>3<nl></otsl></doctag>")

    result = parse(raw)

    assert result.tables == [["A", "B", ""], ["", "2", "3"]]


def test_parse_of_empty_output_is_empty_not_an_error():
    result = parse("<doctag></doctag>")

    assert result.text == ""
    assert result.tables == []
    assert result.formula_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_vlm_doctag.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extractor.vlm.doctag'`

- [ ] **Step 3: Write the implementation**

```python
# extractor/vlm/doctag.py
"""Turn granite-docling's ``<doctag>`` output into internal-model content.

The model does not emit Markdown. It emits a tag stream — ``<text>``,
``<formula>``, ``<otsl>`` (its table format), ``<picture>``, each prefixed by
four ``<loc_N>`` coordinate tokens. This module is the whole translation, and
it is also where output is judged: see ``is_truncated`` and
``formula_is_balanced``, both calibrated on the failures measured in
docs/spike-2026-09-05-granite-docling.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Four coordinate tokens follow every opening tag; they carry layout we do not
# use, because block order alone is the reading order we need.
_LOC = re.compile(r"<loc_\d+>")

# Tags whose text is prose we keep, in the order the model emitted them.
_PROSE_TAGS = ("text", "section_header_level_1", "list_item", "caption", "other")

# The native pipeline detects headers/footers itself (headers_footers.py) and
# keeps them out of the Markdown. Taking the model's copy as prose would put
# them back, in the middle of the page.
_DROPPED_TAGS = ("page_header", "page_footer")

_BLOCK = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL)
_OTSL = re.compile(r"<otsl>(.*?)</otsl>", re.DOTALL)
# OTSL cells are not closed: "<fcel>Uscat<fcel>0 - 0,40". Content runs from one
# cell tag to the next tag of any kind.
_CELL = re.compile(r"<(ched|fcel|ecel|rhed|lcel|ucel|xcel|nl)>([^<]*)")

_SPAN_CELLS = ("lcel", "ucel", "xcel")


@dataclass
class ParsedPage:
    """One page of model output, translated and judged."""

    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    formula_count: int = 0
    rejected_formulas: int = 0
    has_picture: bool = False


def strip_locations(raw: str) -> str:
    """Drop the ``<loc_N>`` coordinate tokens that prefix every tag's content."""
    return _LOC.sub("", raw)


def is_truncated(raw: str) -> bool:
    """True when generation stopped before closing its tags.

    Measured on 10 of 35 garbled pages in the spike. It is the cheapest
    reliable signal that the model fell into a repetition loop and burned the
    token budget — page 176 repeated one fraction until the cap.
    """
    text = raw.strip()
    if not text.endswith("</doctag>"):
        return True
    body = strip_locations(text)
    for tag in set(re.findall(r"<(\w+)>", body)):
        if tag in ("doctag", "nl") or tag.startswith(("loc_", "ched", "fcel")):
            continue
        if f"</{tag}>" in body and body.count(f"<{tag}>") != body.count(f"</{tag}>"):
            return True
    return False


def formula_is_balanced(latex: str) -> bool:
    r"""True when every ``\left`` has a matching ``\right``.

    6 of 127 formulas in the spike were unbalanced. This is deliberately not a
    full LaTeX parse: it catches the observed failure (a truncated or looping
    formula) with no renderer, no Node, and no new dependency.
    """
    return latex.count(r"\left") == latex.count(r"\right")


def _parse_otsl(body: str) -> list[list[str]]:
    """Turn one OTSL table body into rows of cell strings."""
    rows: list[list[str]] = []
    row: list[str] = []
    for tag, content in _CELL.findall(body):
        if tag == "nl":
            rows.append(row)
            row = []
        elif tag in _SPAN_CELLS:
            # A span continuation carries no text of its own; keep the column
            # so rows stay rectangular for the Markdown writer.
            row.append("")
        else:
            row.append(content.strip())
    if row:
        rows.append(row)
    return [r for r in rows if r]


def parse(raw: str) -> ParsedPage:
    """Translate one page of doctag output into prose, tables and counts."""
    result = ParsedPage()
    body = strip_locations(raw)

    for match in _OTSL.finditer(body):
        rows = _parse_otsl(match.group(1))
        if rows:
            result.tables.append(rows)
    # Remove tables before reading prose, so cell text is not emitted twice.
    body = _OTSL.sub("", body)

    result.has_picture = "<picture>" in body

    pieces: list[str] = []
    for tag, content in _BLOCK.findall(body):
        if tag in _DROPPED_TAGS or tag == "picture":
            continue
        content = content.strip()
        if not content:
            continue
        if tag == "formula":
            if formula_is_balanced(content):
                result.formula_count += 1
                pieces.append(f"$$\n{content}\n$$")
            else:
                # Dropped, not emitted broken: a wrong equation that renders is
                # worse than a missing one, because nothing flags it. The caller
                # turns this count into FORMULA_REVIEW_REQUIRED.
                result.rejected_formulas += 1
        elif tag in _PROSE_TAGS:
            pieces.append(content)

    result.text = "\n".join(pieces)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vlm_doctag.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add extractor/vlm/doctag.py tests/test_vlm_doctag.py
git commit -m "Parse and validate granite-docling doctag output"
```

---

### Task 3: The engine

Thin wrapper: render a page, run the model, return raw output. Kept small because it is the one part that cannot be tested without the weights.

**Files:**
- Create: `extractor/vlm/engine.py`
- Test: `tests/test_vlm_apply.py` (engine is exercised through a stub; see Task 4)

**Interfaces:**
- Consumes: `extractor.vlm.config.get_config()`.
- Produces: `GraniteDoclingEngine(model_name: str)` with attributes `name: str` and method `convert(png_bytes: bytes, *, max_tokens: int) -> str` returning raw doctag; module function `render_page(page, *, dpi: int) -> bytes` returning PNG bytes.

- [ ] **Step 1: Write the implementation**

There is no unit test for this file — it does nothing but call two libraries, and asserting that it calls them tests the mock, not the code. Its behaviour is covered end to end by `tests/golden/test_vlm_pages.py` in Task 7, which is marked `slow`.

```python
# extractor/vlm/engine.py
"""granite-docling-258M on mlx — render a page, get its doctag back.

Deliberately thin: everything judgeable lives in doctag.py and apply.py, which
are testable without the weights. Imports of mlx are inside methods so this
module stays importable for tooling on any platform.
"""
from __future__ import annotations

import logging

import pymupdf

from extractor.vlm.config import DEFAULT_MAX_TOKENS

logger = logging.getLogger(__name__)

# The docling conversion prompt the model was trained on. Not a knob: a
# free-form instruction produces prose, not the tag stream doctag.py parses.
PROMPT = "Convert this page to docling."


def render_page(page, *, dpi: int) -> bytes:
    """Rasterize one PyMuPDF page to PNG bytes at *dpi*.

    Whole page, not per-image crops: a formula is assembled from native text,
    small rasters and vector rules, and cropping to embedded images alone
    breaks that assembly apart before the model ever sees it.
    """
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
    return pixmap.tobytes("png")


class GraniteDoclingEngine:
    """Wraps an mlx-vlm model, loaded once and reused for every page."""

    def __init__(self, model_name: str) -> None:
        from mlx_vlm import load

        self.name = model_name
        self._model, self._processor = load(model_name)
        logger.debug("Visual model ready: %s", model_name)

    def convert(self, png_bytes: bytes, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """Return the raw doctag output for one rendered page."""
        import tempfile
        from pathlib import Path

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        # mlx-vlm takes image paths, not buffers.
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            image_path.write_bytes(png_bytes)
            formatted = apply_chat_template(
                self._processor, self._model.config, PROMPT, num_images=1
            )
            result = generate(
                self._model, self._processor, formatted, [str(image_path)],
                max_tokens=max_tokens, verbose=False,
            )
        return result.text if hasattr(result, "text") else str(result)
```

- [ ] **Step 2: Verify it imports and lints**

Run: `python3 -c "import extractor.vlm.engine"` — Expected: no output (mlx imports are deferred, so this works without mlx installed).
Run: `ruff check extractor/vlm/` — Expected: `All checks passed!`

- [ ] **Step 3: Commit**

```bash
git add extractor/vlm/engine.py
git commit -m "Add granite-docling mlx engine and page renderer"
```

---

### Task 4: Routing, caching and warnings

The glue `pdf_reader` calls. Fully tested with a stub engine — no weights needed.

**Files:**
- Create: `extractor/vlm/apply.py`
- Test: `tests/test_vlm_apply.py`

**Interfaces:**
- Consumes: `doctag.parse`, `doctag.is_truncated`, `registry.is_available`, `registry.get_engine`, `registry.unavailable_reason`, `config.get_config`, `engine.render_page`.
- Produces:
  - `should_route(page_class: str, native_chars: int) -> bool`
  - `MIN_NATIVE_CHARS: int` (200)
  - `MIN_YIELD_RATIO: float` (0.5)
  - `vlm_pages(path: Path, page_classes: list[str], native_char_counts: list[int]) -> tuple[dict[int, ParsedPage], list[dict]]` — maps 1-based page number to its accepted `ParsedPage`, plus document-level warnings.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_apply.py
"""Routing, rejection and caching for the visual fallback, with a stub engine."""
import pymupdf
import pytest

from extractor.vlm import apply, config, registry


@pytest.fixture(autouse=True)
def _clean():
    config.reset()
    yield
    config.reset()


@pytest.fixture
def two_page_pdf(tmp_path):
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), "native text on the page")
    out = tmp_path / "doc.pdf"
    doc.save(out)
    doc.close()
    return out


class StubEngine:
    """Returns a canned doctag per call and records what it was asked to do."""

    def __init__(self, outputs):
        self.name = "stub"
        self.outputs = list(outputs)
        self.calls = 0

    def convert(self, png_bytes, *, max_tokens):
        self.calls += 1
        return self.outputs.pop(0)


def _install(monkeypatch, engine, tmp_path):
    config.configure(enabled=True, cache_dir=str(tmp_path / "cache"))
    monkeypatch.setattr(registry, "is_available", lambda: True)
    monkeypatch.setattr(registry, "get_engine", lambda: engine)


GOOD = ("<doctag><text><loc_1><loc_1><loc_1><loc_1>"
        "Recovered prose that is comfortably longer than the native text."
        "</text><formula>a = \\frac { b } { c }</formula></doctag>")

TRUNCATED = "<doctag><formula>\\left[ \\frac{z}{b} \\left[ \\frac{z}{b}"

EMPTY = "<doctag></doctag>"


# --- routing ---------------------------------------------------------------

def test_routes_garbled_pages_regardless_of_length():
    """A garbled page has plenty of characters — they are just wrong. Character
    count cannot judge it; the page class already did."""
    assert apply.should_route("garbled", 5000) is True


def test_routes_thin_non_native_pages():
    assert apply.should_route("layout-complex", 50) is True


def test_does_not_route_healthy_native_text_pages():
    """The spike's page 6: a table of contents the native path handles
    perfectly came back from the model 93% empty."""
    assert apply.should_route("native-text", 20) is False
    assert apply.should_route("native-text", 5000) is False


def test_does_not_route_a_page_that_already_has_text():
    assert apply.should_route("layout-complex", 5000) is False


def test_disabled_routes_nothing(two_page_pdf):
    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "garbled"], [10, 10])

    assert pages == {}
    assert warnings == []


# --- unavailability --------------------------------------------------------

def test_unavailable_warns_once_and_does_not_crash(two_page_pdf, monkeypatch):
    config.configure(enabled=True)
    monkeypatch.setattr(registry, "is_available", lambda: False)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "garbled"], [10, 10])

    assert pages == {}
    assert [w["code"] for w in warnings] == ["VLM_UNAVAILABLE"]


# --- acceptance and rejection ---------------------------------------------

def test_accepted_page_reports_vlm_applied(two_page_pdf, monkeypatch, tmp_path):
    _install(monkeypatch, StubEngine([GOOD]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [10, 900])

    assert set(pages) == {1}
    assert pages[1].formula_count == 1
    applied = [w for w in warnings if w["code"] == "VLM_APPLIED"]
    assert applied == [{"code": "VLM_APPLIED", "page": 1, "formulas": 1}]


def test_truncated_output_is_rejected(two_page_pdf, monkeypatch, tmp_path):
    _install(monkeypatch, StubEngine([TRUNCATED]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [10, 900])

    assert pages == {}
    rejected = [w for w in warnings if w["code"] == "VLM_OUTPUT_REJECTED"]
    assert rejected[0]["page"] == 1
    assert rejected[0]["reason"] == "truncated"


def test_low_yield_output_is_rejected(two_page_pdf, monkeypatch, tmp_path):
    """Page 6's failure mode: the model returns far less than the page holds."""
    _install(monkeypatch, StubEngine([EMPTY]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [900, 900])

    assert pages == {}
    rejected = [w for w in warnings if w["code"] == "VLM_OUTPUT_REJECTED"]
    assert rejected[0]["reason"] == "low-yield"


def test_rejected_formulas_raise_a_review_warning(two_page_pdf, monkeypatch, tmp_path):
    raw = ("<doctag><text><loc_1><loc_1><loc_1><loc_1>"
           "Prose long enough to clear the yield floor comfortably.</text>"
           "<formula>\\left[ \\frac{z}{b}</formula></doctag>")
    _install(monkeypatch, StubEngine([raw]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [10, 900])

    assert set(pages) == {1}
    review = [w for w in warnings if w["code"] == "FORMULA_REVIEW_REQUIRED"]
    assert review == [{"code": "FORMULA_REVIEW_REQUIRED", "page": 1, "count": 1}]


def test_one_page_failing_does_not_lose_the_others(two_page_pdf, monkeypatch, tmp_path):
    class Exploding(StubEngine):
        def convert(self, png_bytes, *, max_tokens):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("out of memory")
            return GOOD

    _install(monkeypatch, Exploding([]), tmp_path)

    pages, warnings = apply.vlm_pages(two_page_pdf, ["garbled", "garbled"], [10, 10])

    assert set(pages) == {2}
    assert any(w["code"] == "VLM_OUTPUT_REJECTED" and w["page"] == 1 for w in warnings)


# --- caching ---------------------------------------------------------------

def test_second_run_reuses_the_cache(two_page_pdf, monkeypatch, tmp_path):
    engine = StubEngine([GOOD, GOOD])
    _install(monkeypatch, engine, tmp_path)

    first, _ = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [10, 900])
    second, _ = apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [10, 900])

    assert engine.calls == 1, "the second run must not re-infer"
    assert first[1].text == second[1].text


def test_changing_max_tokens_invalidates_the_cache(two_page_pdf, monkeypatch, tmp_path):
    engine = StubEngine([GOOD, GOOD])
    _install(monkeypatch, engine, tmp_path)

    apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [10, 900])
    config.configure(max_tokens=99)
    apply.vlm_pages(two_page_pdf, ["garbled", "native-text"], [10, 900])

    assert engine.calls == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_vlm_apply.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extractor.vlm.apply'`

- [ ] **Step 3: Write the implementation**

```python
# extractor/vlm/apply.py
"""The glue every reader calls: decide, infer, cache, judge.

Mirrors ``extractor/ocr/apply.py`` — readers get back content plus warnings and
never talk to the model directly. Each page is inferred in isolation, so one
failure cannot discard the pages already read.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pymupdf

from extractor.vlm import registry
from extractor.vlm.config import get_config
from extractor.vlm.doctag import ParsedPage, is_truncated, parse
from extractor.vlm.engine import render_page

logger = logging.getLogger(__name__)

# Below this many native characters a page is text-deficient. On the reference
# document the fixed table detection dropped the count of such pages from 169
# to 15, so this is now a narrow rule, not a net over half the book.
MIN_NATIVE_CHARS = 200

# The model must return at least this fraction of the native character count.
# Guards the spike's page-6 failure: a table of contents came back 93% empty
# because the model classified the whole page as one table and stopped.
MIN_YIELD_RATIO = 0.5

# A page class the native path handles well. Never routed — the risk of the
# model losing a good page outweighs anything it could add.
_TRUSTED_CLASS = "native-text"


def should_route(page_class: str, native_chars: int) -> bool:
    """True when this page needs the visual fallback.

    ``garbled`` routes regardless of length: those pages have plenty of
    characters and the characters are wrong, so a length test is blind to
    exactly the case the fallback exists for.
    """
    if page_class == _TRUSTED_CLASS:
        return False
    if page_class == "garbled":
        return True
    return native_chars < MIN_NATIVE_CHARS


def _cache_path(png_bytes: bytes) -> Path | None:
    """Where this page's inference is cached, or None when caching is off.

    Keyed on the rendered image rather than the source file, so DPI and any
    future render change are already part of the identity; model and token cap
    are added because they change the output for identical pixels.
    """
    config = get_config()
    root = Path(config.cache_dir) if config.cache_dir else (
        Path.home() / ".cache" / "knowledge-extractor" / "vlm"
    )
    digest = hashlib.sha256(
        png_bytes + f"|{config.model}|{config.max_tokens}".encode()
    ).hexdigest()
    return root / f"{digest}.json"


def _infer(engine, png_bytes: bytes) -> str:
    """Return raw doctag for one page, from cache when possible."""
    path = _cache_path(png_bytes)
    if path is not None and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))["raw"]
        except (OSError, ValueError, KeyError):  # a damaged entry is not fatal
            logger.debug("Ignoring unreadable VLM cache entry: %s", path)

    raw = engine.convert(png_bytes, max_tokens=get_config().max_tokens)

    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"raw": raw}), encoding="utf-8")
        except OSError:  # a read-only cache must not fail the run
            logger.debug("Could not write VLM cache entry: %s", path)
    return raw


def _judge(parsed: ParsedPage, raw: str, native_chars: int) -> str | None:
    """Return a rejection reason, or None when the output is acceptable."""
    if is_truncated(raw):
        return "truncated"
    produced = len(parsed.text) + sum(
        len(cell) for table in parsed.tables for row in table for cell in row
    )
    if produced < MIN_YIELD_RATIO * native_chars:
        return "low-yield"
    if not produced:
        return "empty"
    return None


def vlm_pages(
    path: Path, page_classes: list[str], native_char_counts: list[int],
) -> tuple[dict[int, ParsedPage], list[dict]]:
    """Run the visual fallback over the pages that need it.

    Returns ``({page_number: ParsedPage}, warnings)``. Pages absent from the
    mapping were either not routed or rejected; the warnings say which.
    """
    if not get_config().enabled:
        return {}, []

    targets = [
        i + 1
        for i, (cls, chars) in enumerate(zip(page_classes, native_char_counts, strict=True))
        if should_route(cls, chars)
    ]
    if not targets:
        return {}, []

    if not registry.is_available():
        reason = registry.unavailable_reason()
        return {}, [{
            "code": "VLM_UNAVAILABLE",
            "detail": str(reason) if reason else "",
            "pages": len(targets),
        }]

    engine = registry.get_engine()
    dpi = get_config().dpi
    results: dict[int, ParsedPage] = {}
    warnings: list[dict] = []

    with pymupdf.open(path) as doc:
        for page_number in targets:
            native_chars = native_char_counts[page_number - 1]
            try:
                raw = _infer(engine, render_page(doc[page_number - 1], dpi=dpi))
                parsed = parse(raw)
            except Exception as exc:  # noqa: BLE001 - isolate one page's failure
                logger.warning("Visual fallback failed on page %d: %s", page_number, exc)
                warnings.append({
                    "code": "VLM_OUTPUT_REJECTED", "page": page_number, "reason": "error",
                })
                continue

            rejection = _judge(parsed, raw, native_chars)
            if rejection:
                warnings.append({
                    "code": "VLM_OUTPUT_REJECTED", "page": page_number, "reason": rejection,
                })
                continue

            results[page_number] = parsed
            warnings.append({
                "code": "VLM_APPLIED", "page": page_number,
                "formulas": parsed.formula_count,
            })
            if parsed.rejected_formulas:
                warnings.append({
                    "code": "FORMULA_REVIEW_REQUIRED", "page": page_number,
                    "count": parsed.rejected_formulas,
                })

    return results, warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vlm_apply.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add extractor/vlm/apply.py tests/test_vlm_apply.py
git commit -m "Add visual-fallback routing, caching and rejection rules"
```

---

### Task 5: Wire into the PDF reader, model and writers

**Files:**
- Modify: `extractor/model.py` (after `make_ocr_text_block`, ~line 37), `extractor/pdf_reader.py:329-455`, `extractor/markdown_writer.py:84-92`, `extractor/warning_text.py`
- Test: `tests/test_vlm_pipeline.py` (create)

**Interfaces:**
- Consumes: `apply.vlm_pages`.
- Produces: `model.make_vlm_text_block(raw_text: str) -> dict | None` returning `{"type": "text", "content": ..., "source": "vlm"}`.

**Merge policy** (from the spec's precedence rules):
- On a `garbled` page, the VLM text block **replaces** the native text block. This follows the precedent already set at `pdf_reader.py:410`, where OCR replaces untrusted native text on exactly this page class.
- On any other routed page, the VLM text block is **added** after the native text block.
- VLM tables are emitted **only when the page has no native tables**. Native tables are authoritative.
- The `<picture>` signal is not used to write a caption. Images are already extracted and linked by the existing `_extract_images` path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_pipeline.py
"""The visual fallback's blocks reaching JSON and Markdown."""
import pymupdf
import pytest

from extractor.markdown_writer import write_markdown
from extractor.model import make_vlm_text_block
from extractor.pdf_reader import extract_pdf
from extractor.vlm import apply, config


@pytest.fixture(autouse=True)
def _clean():
    config.reset()
    yield
    config.reset()


@pytest.fixture
def one_page_pdf(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "native text")
    out = tmp_path / "doc.pdf"
    doc.save(out)
    doc.close()
    return out


def test_make_vlm_text_block_marks_its_source():
    block = make_vlm_text_block("Recovered prose")

    assert block == {"type": "text", "content": "Recovered prose", "source": "vlm"}


def test_make_vlm_text_block_is_none_when_empty():
    assert make_vlm_text_block("   ") is None


def test_disabled_leaves_output_unchanged(one_page_pdf):
    """The default path must be byte-identical to today's output."""
    doc = extract_pdf(one_page_pdf)

    assert not [b for b in doc["pages"][0]["content"] if b.get("source") == "vlm"]
    assert not [w for w in doc["document"].get("warnings", []) if w["code"].startswith("VLM")]


def test_vlm_text_is_added_to_the_page(one_page_pdf, monkeypatch):
    from extractor.vlm.doctag import ParsedPage

    parsed = ParsedPage(text="Recovered prose", tables=[[["a", "b"], ["1", "2"]]])
    monkeypatch.setattr(
        apply, "vlm_pages",
        lambda *a, **k: ({1: parsed}, [{"code": "VLM_APPLIED", "page": 1, "formulas": 0}]),
    )
    config.configure(enabled=True)

    doc = extract_pdf(one_page_pdf)
    blocks = doc["pages"][0]["content"]

    assert [b for b in blocks if b.get("source") == "vlm"][0]["content"] == "Recovered prose"
    assert [b for b in blocks if b["type"] == "table"][0]["content"] == [["a", "b"], ["1", "2"]]


def test_markdown_labels_vlm_text(tmp_path):
    model = {
        "document": {"filename": "d.pdf", "warnings": []},
        "pages": [{
            "unit": 1, "unit_type": "page", "has_images": False, "image_count": 0,
            "content": [{"type": "text", "content": "$$\na = b\n$$", "source": "vlm"}],
        }],
    }

    text = write_markdown(model, tmp_path).read_text(encoding="utf-8")

    assert "Recovered by the visual model" in text
    assert "$$\na = b\n$$" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_vlm_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'make_vlm_text_block'`

- [ ] **Step 3: Write the implementation**

In `extractor/model.py`, directly after `make_ocr_text_block`:

```python
def make_vlm_text_block(raw_text: str) -> dict | None:
    """Normalize visual-model output into a text block that records its source.

    Reuses the ``text`` type for the same reason OCR does: the content flows
    through Markdown, metrics and future chunking exactly like native text.
    There is no confidence field — the model does not report one, and the
    accept/reject decision was already made in ``vlm/apply.py``.
    """
    normalized = normalize(raw_text)
    if not normalized:
        return None
    return {"type": "text", "content": normalized, "source": "vlm"}
```

In `extractor/pdf_reader.py`, add to the imports:

```python
from extractor.model import make_vlm_text_block  # add to the existing model import
from extractor.vlm.apply import vlm_pages
```

After the `ocr_by_page, ocr_doc_warnings = _ocr_pages(...)` call (~line 359):

```python
    # Native character count per page decides text-deficiency; the page class
    # decides damage. should_route() needs both.
    native_chars = [len(text) for text in reader_data["page_texts"]]
    vlm_by_page, vlm_doc_warnings = vlm_pages(
        path, reader_data["page_classes"], native_chars,
    )
```

Add `vlm_doc_warnings` to the initial warnings list:

```python
    doc_warnings: list[dict] = list(ocr_doc_warnings) + list(vlm_doc_warnings)
```

Inside the per-page loop, immediately after the existing `if ocr_block: blocks.append(ocr_block)`:

```python
        vlm_parsed = vlm_by_page.get(page_number)
        if vlm_parsed:
            vlm_block = make_vlm_text_block(vlm_parsed.text)
            if vlm_block:
                # On a garbled page the native text is known-untrusted, and the
                # pipeline already replaces it with OCR above. The visual model
                # is the better reading of the same damage, so it wins outright.
                if page_class == "garbled":
                    blocks = [b for b in blocks if b["type"] != "text"]
                blocks.append(vlm_block)
```

Then, replacing the existing native-table loop so VLM tables fill in only when there are none:

```python
        native_tables = tables.get(page_number, [])
        for table in native_tables:
            blocks.append(make_table_block(table["cells"]))
        # Native tables are authoritative: the model's version is only used
        # where detection found nothing at all.
        if vlm_parsed and not native_tables:
            for rows in vlm_parsed.tables:
                blocks.append(make_table_block(rows))
```

In `extractor/markdown_writer.py`, extend the `source` branch at line 86:

```python
                    if block.get("source") == "ocr":
                        confidence = round(block.get("confidence", 0.0) * 100)
                        parts.append(f"> Text recovered by OCR (confidence {confidence}%)")
                        parts.append("")
                    elif block.get("source") == "vlm":
                        parts.append("> Recovered by the visual model — verify against the page image.")
                        parts.append("")
```

In `extractor/warning_text.py`, add to `_TEMPLATES`:

```python
    "VLM_APPLIED": "content recovered by the visual model",
    "VLM_OUTPUT_REJECTED": "the visual model's output for this page was rejected",
    "VLM_UNAVAILABLE": "the visual fallback could not run",
    # Dropped rather than emitted: a wrong equation that renders is worse than
    # a missing one, because nothing flags it.
    "FORMULA_REVIEW_REQUIRED": "a formula could not be validated and was left out — check the page image",
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -m "not slow" -q`
Expected: PASS, all tests including the existing 351. The baseline snapshot must be untouched — the fallback is off by default.

- [ ] **Step 5: Commit**

```bash
git add extractor/model.py extractor/pdf_reader.py extractor/markdown_writer.py \
        extractor/warning_text.py tests/test_vlm_pipeline.py
git commit -m "Merge visual-fallback output into the PDF reader and writers"
```

---

### Task 6: CLI flags and documentation

**Files:**
- Modify: `main.py`, `README.md`, `CHANGELOG.md`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `extractor.vlm.config.configure`.
- Produces: `--vlm`, `--vlm-model`, `--vlm-dpi`, `--vlm-max-tokens` CLI flags.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_vlm_flags_reach_the_config(tmp_path, monkeypatch):
    from extractor.vlm import config as vlm_config

    vlm_config.reset()
    (tmp_path / "in").mkdir()
    main(["--input", str(tmp_path / "in"), "--output", str(tmp_path / "out"),
          "--log-file", str(tmp_path / "t.log"),
          "--vlm", "--vlm-dpi", "300", "--vlm-max-tokens", "2048"])

    assert vlm_config.get_config().enabled is True
    assert vlm_config.get_config().dpi == 300
    assert vlm_config.get_config().max_tokens == 2048
    vlm_config.reset()


def test_vlm_is_off_unless_asked_for(tmp_path):
    from extractor.vlm import config as vlm_config

    vlm_config.reset()
    (tmp_path / "in").mkdir()
    main(["--input", str(tmp_path / "in"), "--output", str(tmp_path / "out"),
          "--log-file", str(tmp_path / "t.log")])

    assert vlm_config.get_config().enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cli.py -k vlm -v`
Expected: FAIL — `error: unrecognized arguments: --vlm`

- [ ] **Step 3: Write the implementation**

In `main.py`, after the existing `ocr_group` block (~line 78):

```python
    vlm_group = parser.add_argument_group("Visual fallback (Apple Silicon only)")
    vlm_group.add_argument(
        "--vlm", action="store_true",
        help="run a local vision model over damaged/empty pages (~14s per page)",
    )
    vlm_group.add_argument(
        "--vlm-model", default=VLM_DEFAULT_MODEL,
        help=f"model id to load (default: {VLM_DEFAULT_MODEL})",
    )
    vlm_group.add_argument(
        "--vlm-dpi", type=int, default=VLM_DEFAULT_DPI,
        help=f"page render DPI for the visual model (default: {VLM_DEFAULT_DPI})",
    )
    vlm_group.add_argument(
        "--vlm-max-tokens", type=int, default=VLM_DEFAULT_MAX_TOKENS,
        help=f"generation cap per page (default: {VLM_DEFAULT_MAX_TOKENS})",
    )
```

With the imports at the top of `main.py`:

```python
from extractor.vlm.config import DEFAULT_DPI as VLM_DEFAULT_DPI
from extractor.vlm.config import DEFAULT_MAX_TOKENS as VLM_DEFAULT_MAX_TOKENS
from extractor.vlm.config import DEFAULT_MODEL as VLM_DEFAULT_MODEL
from extractor.vlm.config import configure as configure_vlm
```

Beside the existing `configure(...)` call (~line 266):

```python
    configure_vlm(
        enabled=args.vlm,
        model=args.vlm_model,
        dpi=args.vlm_dpi,
        max_tokens=args.vlm_max_tokens,
    )
```

Add to the README's Warning Codes table:

```markdown
| `VLM_APPLIED` | content on this page was recovered by the visual model |
| `VLM_OUTPUT_REJECTED` | the visual model's output was truncated, empty, or far shorter than the page — it was discarded |
| `VLM_UNAVAILABLE` | the visual fallback was requested but could not run (see the detail) |
| `FORMULA_REVIEW_REQUIRED` | a formula failed validation and was left out — check the page image |
```

And a README section after the OCR section:

```markdown
### Visual fallback (optional, Apple Silicon)

Pages whose native text is damaged (`garbled`) or nearly absent can be routed
through a local vision-language model, which reconstructs formulas as LaTeX and
recovers structure that line-level OCR cannot. Off by default.

    pip install -e ".[vlm]"
    python main.py --vlm

The model (`ibm-granite/granite-docling-258M-mlx`, ~500 MB) downloads once on
first use and then runs entirely locally. Expect ~14 s per routed page; results
are cached under `~/.cache/knowledge-extractor/vlm/`, so re-running a document
after changing unrelated settings costs nothing.

Native text and native tables stay authoritative — the model's output is added
alongside them, and replaces them only on `garbled` pages, where the native text
is already known to be untrustworthy. Output that arrives truncated, empty, or
far shorter than the page is discarded with a `VLM_OUTPUT_REJECTED` warning
rather than merged.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_cli.py -v` — Expected: PASS.
Run: `python3 -m pytest -m "not slow" -q` — Expected: PASS.
Run: `ruff check .` — Expected: only the pre-existing `SIM102` in `rotated_text.py`.

- [ ] **Step 5: Commit**

```bash
git add main.py README.md CHANGELOG.md tests/test_cli.py
git commit -m "Add --vlm CLI flags and document the visual fallback"
```

---

### Task 7: Golden-page regression test

Locks in the spike's measured behaviour so a future model, DPI or threshold change is measurable. Marked `slow` — it needs the real weights.

**Files:**
- Create: `tests/golden/test_vlm_pages.py`
- Modify: `CLAUDE.md` (testing notes)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

**Note on page numbers:** every page below is a **PDF index**, not a printed page number. They differ in the reference document and not by a constant offset (PDF page 46 prints "34"). Never translate these.

- [ ] **Step 1: Write the test**

```python
# tests/golden/test_vlm_pages.py
"""Real-model regression on pages the 2026-09-05 spike measured.

Skipped unless the reference document and the model are both present, and
marked slow — it loads real weights. See
docs/spike-2026-09-05-granite-docling.md for the numbers asserted here.

Page numbers are PDF indices, not printed page numbers; they differ in this
document and not by a constant offset.
"""
from pathlib import Path

import pytest

from extractor.vlm import apply, config, registry
from extractor.vlm.doctag import formula_is_balanced, parse

REFERENCE = Path("input/Geotehnica - note de curs.pdf")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not REFERENCE.exists(), reason="reference document not present"),
]


@pytest.fixture(scope="module")
def engine():
    config.configure(enabled=True)
    if not registry.is_available():
        pytest.skip(f"visual fallback unavailable: {registry.unavailable_reason()}")
    yield registry.get_engine()
    config.reset()


@pytest.fixture(scope="module")
def converted(engine):
    """Run the model once over the golden pages and reuse the result."""
    import pymupdf

    from extractor.vlm.engine import render_page

    pages = {}
    with pymupdf.open(REFERENCE) as doc:
        for number in (6, 73, 176):
            png = render_page(doc[number - 1], dpi=config.get_config().dpi)
            pages[number] = apply._infer(engine, png)
    return pages


def test_garbled_formula_page_yields_balanced_latex(converted):
    """PDF page 73: the native path flattens these formulas to token soup."""
    result = parse(converted[73])

    assert result.formula_count >= 8
    assert result.rejected_formulas == 0
    assert "$$" in result.text


def test_healthy_contents_page_is_never_routed():
    """PDF page 6 is a table of contents the native path handles perfectly; the
    model returned 7% of it. Routing must exclude it by class alone."""
    assert apply.should_route("native-text", 4556) is False


def test_looping_page_is_rejected_not_merged(converted):
    """PDF page 176: one Egorov formula degenerates into a repeating fragment."""
    raw = converted[176]
    result = parse(raw)
    rejection = apply._judge(result, raw, native_chars=1742)

    if rejection is None:
        # A higher token cap may now let this page terminate; if it does, the
        # formulas it emits still have to be well-formed.
        assert all(
            formula_is_balanced(f)
            for f in __import__("re").findall(r"<formula>(.*?)</formula>", raw, 16)
        )
    else:
        assert rejection == "truncated"
```

- [ ] **Step 2: Run it**

Run: `python3 -m pytest tests/golden/test_vlm_pages.py -v -m slow`
Expected: PASS, or SKIP with a clear reason on a machine without the model or the document.

Run: `python3 -m pytest -m "not slow" -q`
Expected: PASS — the golden test is excluded from the fast suite.

- [ ] **Step 3: Update CLAUDE.md**

Add to the "Testing notes" section:

```markdown
- `tests/test_vlm_doctag.py` and `tests/test_vlm_apply.py` cover the visual
  fallback's parsing, routing and rejection with captured model output and a
  stub engine, so they need no weights. `tests/golden/test_vlm_pages.py` is the
  only test that loads the real model; it is marked `slow` and skips when the
  reference document or the model is absent. Prefer the stub pattern.
```

Add a new architecture section after the OCR one:

```markdown
**The visual fallback (`extractor/vlm/`) is a second optional layer, same shape
as OCR.** `config.py` / `registry.py` / `apply.py` mirror their OCR
counterparts exactly. It routes only pages the native path cannot serve —
`page_class == "garbled"`, or under `MIN_NATIVE_CHARS` and not `native-text` —
and it renders the *whole page*, never per-image crops: a formula is assembled
from native text, small rasters and vector rules, and cropping to embedded
images breaks that assembly before the model sees it. A `native-text` page is
never routed, however thin: a table of contents measured at 4,556 native
characters came back from the model 93% empty. Output is rejected when it is
truncated (generation hit the token cap, usually a repetition loop) or yields
under `MIN_YIELD_RATIO` of the page's native characters, and an individual
formula is dropped when its `\left`/`\right` counts disagree — a wrong equation
that renders is worse than a missing one, because nothing flags it. Every
inference is cached on disk keyed by rendered pixels + model + token cap, since
a routed page costs ~14 s.
```

- [ ] **Step 4: Commit**

```bash
git add tests/golden/test_vlm_pages.py CLAUDE.md
git commit -m "Add golden-page regression for the visual fallback"
```

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
|---|---|
| Full-page render, not per-image crops | 3 (`render_page` docstring), 7 (CLAUDE.md) |
| Route by existing page class, no new detector | 4 (`should_route`) |
| Never route a healthy `native-text` page (page-6 failure) | 4, 7 |
| Reject truncated / unclosed output (10 of 35 pages) | 2 (`is_truncated`), 4 (`_judge`) |
| Reject unbalanced formulas (6 of 127) | 2 (`formula_is_balanced`), 4 |
| No KaTeX/Node validation | 2 — string checks only |
| No watermark-masked render | Not implemented; spec finding 3 measured zero leakage |
| Native text/tables authoritative | 5 (merge policy) |
| `garbled` pages may be replaced | 5, following `pdf_reader.py:410` precedent |
| No captions invented from low-confidence output | 2 (`<picture>` produces no text), 5 |
| Cache keyed on render + model + settings | 4 (`_cache_path`) |
| Optional dependency, graceful absence | 1 (`registry`), 6 (extra) |
| Windows unaffected | 1 (`UNSUPPORTED_PLATFORM`), 6 (off by default) |
| doctag → internal model conversion | 2 |
| Structured warnings in log, summary, Markdown | 5 (`warning_text.py`) |
| Golden regression pinned by PDF index | 7 |

**Deliberately not built** (spec says measure first): crop-level routing to formula/chart/figure modes, ONNX/Windows engine, a formula-specific detector, `WATERMARK_MASK_APPLIED`, and separate original/cleaned render variants.

**Open item for Task 6:** `DEFAULT_MAX_TOKENS` is raised to 8192 from the spike's 4096 on the reasoning that 10 of 35 pages hit the cap mid-formula. This is a prediction, not a measurement. After Task 6, re-run the 35 garbled pages and confirm the truncation count drops; if it does not, page 176's loop is a model limit rather than a budget limit, and the `truncated` rejection is doing its job correctly.

---

## Amendment, 2026-09-05: figure regions land first (done), and what it leaves open

Task 0 (committed separately, before this plan runs): a page's pictures are now
grouped into *figures* and rendered from the page — see
`raster.page_image_regions`. On the Geotehnica corpus that turned 902 emitted
images into 553 figures; the eight-band diagram on page 17 is one file again.
Both consumers (`pdf_reader._extract_images`, `ocr.raster.page_images_to_arrays`)
share it, so OCR reads whole figures rather than strips.

That fixes fragmentation. It does **not** fix the second complaint that
prompted it — a figure that is complete but carries no surrounding caption. The
pages showing that (16, 21, 37 of the Geotehnica PDF) are `native-text` with
~1,000 native characters each, so the routing rule above will never send them:
`garbled` is false and `MIN_NATIVE_CHARS` is far below. Rendering the whole
page is exactly what those pages need, and this layer already does it.

Open decision for Task 4, not yet made: whether to add an image-dense routing
criterion (e.g. two or more regions covering more than ~25% of the page) that
routes such a page despite `native-text`. Against it stands the measured reason
`native-text` is excluded at all — the 4,556-character table of contents that
came back 93% empty. Any such criterion needs its own measurement pass before
it goes in; do not add it on the strength of this note.
