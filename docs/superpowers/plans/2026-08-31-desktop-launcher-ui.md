# Desktop Launcher + Local Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a non-technical user convert a folder of mixed documents to JSON and Markdown by double-clicking `Start.bat`, and tell them clearly which results they can trust.

**Architecture:** A new `ui/` package and a `launcher/` bootstrap sit *on top* of the existing pipeline. `ui/jobs.py` imports `process_file` from `main.py` and runs it on a worker thread; `ui/quality.py` turns each finished document into statistics and a pass/review/fail verdict; `ui/server.py` is a stdlib HTTP server on loopback serving one page. Nothing under `extractor/` and nothing in `main.py` is modified.

**Tech Stack:** Python 3.11+ standard library only for the UI (`http.server`, `threading`, `tkinter`, `json`, `secrets`). No new runtime dependencies. PowerShell for the bootstrap. Plain HTML/CSS/JS, no framework, no CDN.

**Spec:** `docs/superpowers/specs/2026-08-31-desktop-launcher-ui-design.md`

## Global Constraints

- **Do not modify any file under `extractor/`, or `main.py`.** If a task appears to need such a change, stop and report it — this rule is the entire safety argument for the add-on.
- **No new runtime dependencies.** UI code imports only the Python standard library plus the existing pipeline.
- The full existing test suite must pass unchanged after every task: `.venv/Scripts/python.exe -m pytest -m "not slow"`.
- `.venv/Scripts/ruff check .` must be clean after every task. Line length 100. Ruff rules in force: `E, W, F, I, UP, B, BLE, SIM, RUF`.
- Python floor is 3.11 (`requires-python = ">=3.11"`). Modern syntax (`X | None`, `list[str]`) is expected.
- Target platform is Windows. Paths in tests use `tmp_path`, never hardcoded separators.
- All user-facing wording comes from `extractor.warning_text.describe`. Never invent a second phrasing for a warning code.
- Verdict names are exactly the strings `"pass"`, `"review"`, `"fail"`.
- Output goes to `<source folder>/Extracted/json/` and `<source folder>/Extracted/markdown/`.
- Commit after every task.

## Reference: existing API this plan consumes

Read these before starting; the signatures are copied here so no task has to go looking.

```python
# main.py
def process_file(path: Path, json_dir: Path, md_dir: Path) -> tuple[Path, Path, dict]
    # returns (json_path, md_path, model); logs; raises on reader failure

# extractor/limits.py
MAX_FILE_MB: int = 50
MAX_PAGES: int = 500
class FileTooLargeError(Exception): ...          # str(exc) is user-readable
def check_file_size(path: Path, max_bytes: int | None = None) -> None

# extractor/dispatcher.py
SUPPORTED_EXTENSIONS: set[str]                   # lowercase, with leading dot

# extractor/warning_text.py
def describe(warning: dict) -> str               # one plain sentence, "Page N: ..." prefixed

# extractor/ocr/config.py
DEFAULT_DPI = 300
DEFAULT_MIN_CONFIDENCE = 0.60
def configure(**overrides) -> OcrConfig          # enabled, model_dir, dpi, min_confidence
def get_config() -> OcrConfig

# extractor/ocr/registry.py
def is_available() -> bool
def unavailable_reason() -> OcrUnavailable | None   # str(exc) is user-readable
```

The model shape returned by `process_file` (from `extractor/model.py`):

```python
{
  "document": {"filename": str, "source_type": str, "pages": int, "author": str,
               "title": str, "has_images": bool, "image_count": int,
               "schema_version": "2.0",
               "warnings": [ {"code": str, "page": int, ...} ]},   # key absent when empty
  "pages": [ {"unit": int, "unit_type": str, "has_images": bool, "image_count": int,
              "content": [ ... ], "page_class": str} ]             # page_class optional
}
```

Block shapes inside `unit["content"]`:
- `{"type": "text", "content": str}` — and OCR blocks add `"source": "ocr"`, `"confidence": float`
- `{"type": "header" | "footer", "content": str}`
- `{"type": "table", "content": [[str, ...], ...]}`

## File Structure

| File | Responsibility |
|---|---|
| `ui/__init__.py` | Empty package marker. |
| `ui/quality.py` | Pure functions: model dict → statistics, verdict, notes, preview. No I/O. |
| `ui/selection.py` | Folder/file list → supported files, ignored files, `Extracted/` output paths. |
| `ui/jobs.py` | One conversion run on a worker thread: progress by bytes, durations, stop flag, result rows. |
| `ui/report.py` | Result rows → `report.md` and `report.json` in the `Extracted` folder. |
| `ui/picker.py` | Native Windows folder/file dialogs, run out of process. |
| `ui/server.py` | Route table, token auth, static files, browser launch, idle shutdown. |
| `ui/static/index.html` | The single page. |
| `ui/static/style.css` | Light/dark, minimal. |
| `ui/static/app.js` | Selection → convert → poll → render. |
| `launcher/setup.ps1` | Idempotent bootstrap: find/install Python, venv, deps, launch. |
| `Start.bat` | Three-line shim that calls `setup.ps1`. |
| `tests/test_ui_quality.py` | Gate table and statistics. |
| `tests/test_ui_selection.py` | Scan and output-path rules. |
| `tests/test_ui_jobs.py` | Worker behaviour with a stubbed `process_file`. |
| `tests/test_ui_report.py` | Report contents. |
| `tests/test_ui_picker.py` | Dialog subprocess stubbed. |
| `tests/test_ui_server.py` | Routes, token, path safety. |

Tasks 1–6 are pure Python and fully tested. Task 7 (the page) and Task 8 (the bootstrap) are verified by hand — that is stated in the spec and is not a gap.

---

### Task 1: Quality statistics and gates

**Files:**
- Create: `ui/__init__.py`
- Create: `ui/quality.py`
- Test: `tests/test_ui_quality.py`

**Interfaces:**
- Consumes: `extractor.warning_text.describe`; the model dict shape above.
- Produces:
  - `PASS = "pass"`, `REVIEW = "review"`, `FAIL = "fail"`
  - `UNREADABLE_CODES: frozenset[str]`, `REVIEW_CODES: frozenset[str]`
  - `collect_stats(model: dict) -> dict`
  - `decide_verdict(model: dict, stats: dict, *, min_confidence: float) -> str`
  - `collect_notes(model: dict) -> list[str]`
  - `make_preview(model: dict, limit: int = 200) -> str`
  - `assess(model: dict, *, min_confidence: float) -> dict` returning `{"stats", "verdict", "notes", "preview"}`

- [ ] **Step 1: Create the package marker**

```bash
mkdir ui
```

Create `ui/__init__.py` containing exactly:

```python
"""Local web UI for the extraction pipeline — a layer above it, never inside it."""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ui_quality.py`:

```python
"""Statistics and quality gates over a finished document model."""
import pytest

from ui import quality


def build_model(units, *, warnings=None, filename="doc.pdf", image_count=0):
    """Assemble a minimal model dict of the shape process_file returns."""
    document = {
        "filename": filename,
        "source_type": "pdf",
        "pages": len(units),
        "author": "",
        "title": "",
        "has_images": image_count > 0,
        "image_count": image_count,
        "schema_version": "2.0",
    }
    if warnings:
        document["warnings"] = warnings
    return {"document": document, "pages": units}


def text_unit(number, *blocks):
    return {"unit": number, "unit_type": "page", "has_images": False,
            "image_count": 0, "content": list(blocks)}


# --- statistics -----------------------------------------------------------

def test_counts_characters_across_text_blocks():
    model = build_model([
        text_unit(1, {"type": "text", "content": "hello"}),
        text_unit(2, {"type": "text", "content": "worlds"}),
    ])
    stats = quality.collect_stats(model)
    assert stats["characters"] == 11
    assert stats["text_blocks"] == 2


def test_counts_table_cells_and_ignores_them_in_characters():
    model = build_model([
        text_unit(1, {"type": "table", "content": [["a", "b"], ["c", "d"], ["e", "f"]]}),
    ])
    stats = quality.collect_stats(model)
    assert stats["table_blocks"] == 1
    assert stats["table_cells"] == 6
    assert stats["characters"] == 0


def test_header_and_footer_blocks_do_not_count_as_text():
    model = build_model([
        text_unit(1,
                  {"type": "header", "content": "Company confidential"},
                  {"type": "text", "content": "body"},
                  {"type": "footer", "content": "page 1"}),
    ])
    stats = quality.collect_stats(model)
    assert stats["text_blocks"] == 1
    assert stats["characters"] == 4


def test_averages_ocr_confidence_over_ocr_blocks_only():
    model = build_model([
        text_unit(1,
                  {"type": "text", "content": "native"},
                  {"type": "text", "content": "scan", "source": "ocr", "confidence": 0.90},
                  {"type": "text", "content": "scan", "source": "ocr", "confidence": 0.70}),
    ])
    stats = quality.collect_stats(model)
    assert stats["ocr_confidence"] == pytest.approx(0.80)


def test_ocr_confidence_is_none_without_ocr_blocks():
    model = build_model([text_unit(1, {"type": "text", "content": "native"})])
    assert quality.collect_stats(model)["ocr_confidence"] is None


def test_estimated_tokens_is_characters_over_four():
    model = build_model([text_unit(1, {"type": "text", "content": "x" * 400})])
    assert quality.collect_stats(model)["est_tokens"] == 100


def test_counts_pages_read_by_ocr():
    model = build_model(
        [text_unit(1), text_unit(2)],
        warnings=[{"code": "OCR_APPLIED", "page": 1, "confidence": 0.9}],
    )
    assert quality.collect_stats(model)["ocr_pages"] == 1


# --- verdicts -------------------------------------------------------------

def test_empty_document_fails_even_though_extraction_succeeded():
    """The silent failure this whole layer exists to catch."""
    model = build_model([text_unit(1)])
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.FAIL


def test_table_only_document_passes():
    model = build_model([text_unit(1, {"type": "table", "content": [["a"]]})])
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.PASS


def test_low_confidence_ocr_is_review():
    model = build_model(
        [text_unit(1, {"type": "text", "content": "body"})],
        warnings=[{"code": "OCR_LOW_CONFIDENCE", "page": 1, "confidence": 0.4}],
    )
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.REVIEW


def test_informational_codes_do_not_downgrade_a_good_document():
    """A gate that fires on ordinary documents trains the user to ignore it."""
    model = build_model(
        [text_unit(1, {"type": "text", "content": "body"})],
        warnings=[{"code": "HEADER_FOOTER_DETECTED", "page": 1},
                  {"code": "LAYOUT_COMPLEX", "page": 1},
                  {"code": "POSSIBLE_TWO_COLUMN_ORDER", "page": 1}],
    )
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.PASS


def test_most_pages_unreadable_is_fail():
    model = build_model(
        [text_unit(1, {"type": "text", "content": "body"}), text_unit(2), text_unit(3)],
        warnings=[{"code": "OCR_UNAVAILABLE", "page": 2},
                  {"code": "OCR_FAILED", "page": 3}],
    )
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.FAIL


def test_some_pages_unreadable_is_review():
    model = build_model(
        [text_unit(1, {"type": "text", "content": "body"}), text_unit(2), text_unit(3)],
        warnings=[{"code": "OCR_UNAVAILABLE", "page": 3}],
    )
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.REVIEW


def test_average_ocr_confidence_below_threshold_is_review():
    model = build_model([
        text_unit(1, {"type": "text", "content": "scan",
                      "source": "ocr", "confidence": 0.4}),
    ])
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.REVIEW


def test_unknown_warning_code_does_not_crash_the_verdict():
    model = build_model(
        [text_unit(1, {"type": "text", "content": "body"})],
        warnings=[{"code": "SOMETHING_NEW_2027", "page": 1}],
    )
    stats = quality.collect_stats(model)
    assert quality.decide_verdict(model, stats, min_confidence=0.6) == quality.PASS


# --- notes and preview ----------------------------------------------------

def test_notes_are_sentences_not_codes():
    model = build_model(
        [text_unit(1, {"type": "text", "content": "body"})],
        warnings=[{"code": "SCANNED_PAGE_NO_TEXT", "page": 2}],
    )
    notes = quality.collect_notes(model)
    assert notes == ["Page 2: scanned page — the file itself carries no text layer"]


def test_notes_are_empty_without_warnings():
    assert quality.collect_notes(build_model([text_unit(1)])) == []


def test_preview_truncates_with_an_ellipsis():
    model = build_model([text_unit(1, {"type": "text", "content": "x" * 500})])
    preview = quality.make_preview(model, limit=200)
    assert len(preview) == 201
    assert preview.endswith("…")


def test_preview_joins_short_blocks_without_truncating():
    model = build_model([
        text_unit(1, {"type": "text", "content": "first"}),
        text_unit(2, {"type": "text", "content": "second"}),
    ])
    assert quality.make_preview(model) == "first second"


def test_assess_returns_every_part_in_one_call():
    model = build_model([text_unit(1, {"type": "text", "content": "body"})])
    result = quality.assess(model, min_confidence=0.6)
    assert set(result) == {"stats", "verdict", "notes", "preview"}
    assert result["verdict"] == quality.PASS
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_quality.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'ui.quality'`.

- [ ] **Step 4: Implement `ui/quality.py`**

```python
"""Statistics and quality verdicts for one finished document.

Pure functions over the model dict — no I/O, no threads, no imports from the
UI. The gates live in one place because a threshold written twice is a
threshold that will disagree with itself.
"""
from __future__ import annotations

from extractor.warning_text import describe

PASS = "pass"
REVIEW = "review"
FAIL = "fail"

# Content exists in the file but was not extracted. Enough of these and the
# output is not usable, whatever the reader reported.
UNREADABLE_CODES = frozenset({
    "OCR_UNAVAILABLE", "OCR_SKIPPED_DISABLED", "OCR_MODEL_INCOMPATIBLE", "OCR_FAILED",
})

# Output exists but something about it is not trustworthy.
# Page-limit truncation is deliberately absent: MAX_PAGES is defined but never
# applied by any reader, so a rule for it could never fire. Add the code here
# on the day the pipeline starts emitting one.
REVIEW_CODES = frozenset({
    "OCR_LOW_CONFIDENCE", "OCR_MIXED_CONFIDENCE", "OCR_NOISE_FILTERED",
    "GARBLED_TEXT", "ENCODING_FALLBACK",
})

# Characters per token. Crude on purpose: the user's next step is chunking for
# an LLM, where an order of magnitude helps and precision does not.
CHARS_PER_TOKEN = 4


def collect_stats(model: dict) -> dict:
    """Count what was extracted. Header and footer blocks are not content."""
    document = model["document"]
    characters = 0
    text_blocks = 0
    table_blocks = 0
    table_cells = 0
    confidences: list[float] = []

    for unit in model["pages"]:
        for block in unit["content"]:
            if block["type"] == "text":
                text_blocks += 1
                characters += len(block["content"])
                if block.get("source") == "ocr":
                    confidences.append(block["confidence"])
            elif block["type"] == "table":
                table_blocks += 1
                table_cells += sum(len(row) for row in block["content"])

    warnings = document.get("warnings", [])
    return {
        "units": document["pages"],
        "text_blocks": text_blocks,
        "table_blocks": table_blocks,
        "characters": characters,
        "table_cells": table_cells,
        "images": document["image_count"],
        "ocr_pages": sum(1 for w in warnings if w["code"] == "OCR_APPLIED"),
        "ocr_confidence": (sum(confidences) / len(confidences)) if confidences else None,
        "est_tokens": characters // CHARS_PER_TOKEN,
    }


def _unreadable_pages(model: dict) -> int:
    """Number of distinct pages that reported unextracted content."""
    pages = {
        warning.get("page")
        for warning in model["document"].get("warnings", [])
        if warning["code"] in UNREADABLE_CODES
    }
    return len(pages)


def decide_verdict(model: dict, stats: dict, *, min_confidence: float) -> str:
    """Return PASS, REVIEW or FAIL for one document."""
    codes = {w["code"] for w in model["document"].get("warnings", [])}

    if stats["characters"] == 0 and stats["table_cells"] == 0:
        return FAIL
    unreadable = _unreadable_pages(model)
    if unreadable and unreadable * 2 > stats["units"]:
        return FAIL

    if codes & REVIEW_CODES or unreadable:
        return REVIEW
    confidence = stats["ocr_confidence"]
    if confidence is not None and confidence < min_confidence:
        return REVIEW
    return PASS


def collect_notes(model: dict) -> list[str]:
    """Every warning as one plain sentence, in the order the reader raised them."""
    return [describe(warning) for warning in model["document"].get("warnings", [])]


def make_preview(model: dict, limit: int = 200) -> str:
    """First *limit* characters of extracted text — the fastest trust check.

    Garbled OCR is obvious at a glance here, without opening any file.
    """
    parts: list[str] = []
    length = 0
    for unit in model["pages"]:
        for block in unit["content"]:
            if block["type"] != "text":
                continue
            parts.append(block["content"])
            length += len(block["content"]) + 1
            if length > limit:
                return " ".join(parts)[:limit] + "…"
    return " ".join(parts)


def assess(model: dict, *, min_confidence: float) -> dict:
    """Everything the UI needs to know about one finished document."""
    stats = collect_stats(model)
    return {
        "stats": stats,
        "verdict": decide_verdict(model, stats, min_confidence=min_confidence),
        "notes": collect_notes(model),
        "preview": make_preview(model),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_quality.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and run the whole suite**

Run: `.venv/Scripts/ruff check .` — expected: clean.
Run: `.venv/Scripts/python.exe -m pytest -m "not slow"` — expected: all PASS, no change to existing tests.

- [ ] **Step 7: Commit**

```bash
git add ui/__init__.py ui/quality.py tests/test_ui_quality.py
git commit -m "Add quality statistics and pass/review/fail gates

A document that extracts to zero characters is reported as failed rather
than converted -- the silent failure the UI exists to catch. Informational
codes never downgrade a file, because a gate that fires on ordinary
documents trains the user to ignore every gate."
```

---

### Task 2: Selection scan and output paths

**Files:**
- Create: `ui/selection.py`
- Test: `tests/test_ui_selection.py`

**Interfaces:**
- Consumes: `extractor.dispatcher.SUPPORTED_EXTENSIONS`.
- Produces:
  - `class Selection` (frozen dataclass): `files: list[Path]`, `ignored: list[Path]`, `root: Path | None`
  - `Selection.total_bytes() -> int`
  - `Selection.ignored_extensions() -> list[str]`
  - `scan_folder(folder: Path) -> Selection`
  - `scan_files(paths: list[Path]) -> Selection`
  - `extracted_root(source_file: Path) -> Path`
  - `output_dirs(source_file: Path) -> tuple[Path, Path]` returning `(json_dir, md_dir)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_selection.py`:

```python
"""Turning a folder or a file list into what the run will actually process."""
from pathlib import Path

from ui import selection


def make_files(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


def test_scan_folder_splits_supported_from_ignored(tmp_path):
    make_files(tmp_path, "a.pdf", "b.docx", "c.doc", "notes.rtf")
    result = selection.scan_folder(tmp_path)
    assert [p.name for p in result.files] == ["a.pdf", "b.docx"]
    assert [p.name for p in result.ignored] == ["c.doc", "notes.rtf"]


def test_scan_folder_is_not_recursive(tmp_path):
    make_files(tmp_path, "a.pdf", "sub/b.pdf")
    assert [p.name for p in selection.scan_folder(tmp_path).files] == ["a.pdf"]


def test_scan_folder_sorts_files(tmp_path):
    make_files(tmp_path, "z.pdf", "a.pdf", "m.pdf")
    assert [p.name for p in selection.scan_folder(tmp_path).files] == [
        "a.pdf", "m.pdf", "z.pdf"]


def test_scan_folder_matches_extensions_case_insensitively(tmp_path):
    make_files(tmp_path, "SCAN.PDF")
    assert [p.name for p in selection.scan_folder(tmp_path).files] == ["SCAN.PDF"]


def test_scan_folder_records_the_root(tmp_path):
    make_files(tmp_path, "a.pdf")
    assert selection.scan_folder(tmp_path).root == tmp_path


def test_scan_folder_on_empty_folder_yields_nothing(tmp_path):
    result = selection.scan_folder(tmp_path)
    assert result.files == []
    assert result.ignored == []


def test_scan_files_keeps_only_supported_and_has_no_root(tmp_path):
    make_files(tmp_path, "a.pdf", "b.doc")
    result = selection.scan_files([tmp_path / "a.pdf", tmp_path / "b.doc"])
    assert [p.name for p in result.files] == ["a.pdf"]
    assert [p.name for p in result.ignored] == ["b.doc"]
    assert result.root is None


def test_scan_files_drops_paths_that_do_not_exist(tmp_path):
    make_files(tmp_path, "a.pdf")
    result = selection.scan_files([tmp_path / "a.pdf", tmp_path / "gone.pdf"])
    assert [p.name for p in result.files] == ["a.pdf"]


def test_total_bytes_sums_the_supported_files(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"12345")
    (tmp_path / "b.pdf").write_bytes(b"123")
    assert selection.scan_folder(tmp_path).total_bytes() == 8


def test_ignored_extensions_are_deduplicated_and_sorted(tmp_path):
    make_files(tmp_path, "a.doc", "b.doc", "c.rtf")
    assert selection.scan_folder(tmp_path).ignored_extensions() == [".doc", ".rtf"]


def test_output_dirs_sit_beside_the_source_file(tmp_path):
    source = tmp_path / "reports" / "a.pdf"
    source.parent.mkdir()
    source.write_text("x", encoding="utf-8")
    json_dir, md_dir = selection.output_dirs(source)
    assert json_dir == tmp_path / "reports" / "Extracted" / "json"
    assert md_dir == tmp_path / "reports" / "Extracted" / "markdown"


def test_extracted_root_is_the_parent_of_both_output_dirs(tmp_path):
    source = tmp_path / "a.pdf"
    json_dir, md_dir = selection.output_dirs(source)
    root = selection.extracted_root(source)
    assert json_dir.parent == root
    assert md_dir.parent == root
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_selection.py -v`
Expected: `ModuleNotFoundError: No module named 'ui.selection'`.

- [ ] **Step 3: Implement `ui/selection.py`**

```python
"""What a run will process, and where its output goes.

``main.discover_inputs`` answers only half of this: it returns the supported
files and says nothing about the rest. The UI has to be able to answer "where
is my .doc file?" before the user asks it, so the scan lives here and reports
both halves.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from extractor.dispatcher import SUPPORTED_EXTENSIONS

EXTRACTED_DIRNAME = "Extracted"


@dataclass(frozen=True)
class Selection:
    """The supported files a run will process, and the ones it will skip."""

    files: list[Path]
    ignored: list[Path]
    root: Path | None

    def total_bytes(self) -> int:
        """Combined size of the supported files — the progress denominator."""
        return sum(path.stat().st_size for path in self.files)

    def ignored_extensions(self) -> list[str]:
        """Distinct extensions that were skipped, for the 'files ignored' line."""
        return sorted({path.suffix.lower() for path in self.ignored})


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def scan_folder(folder: Path) -> Selection:
    """Split one folder's files into supported and ignored. Not recursive.

    Subfolders are left alone: a user who picks a folder means that folder,
    and silently walking into archives or backup subdirectories would convert
    far more than they asked for.
    """
    entries = sorted(path for path in folder.iterdir() if path.is_file())
    return Selection(
        files=[p for p in entries if _is_supported(p)],
        ignored=[p for p in entries if not _is_supported(p)],
        root=folder,
    )


def scan_files(paths: list[Path]) -> Selection:
    """Split an explicit file selection. Missing paths are dropped silently.

    ``root`` is None because the files may come from several folders; output
    still lands beside each individual file.
    """
    existing = [path for path in paths if path.is_file()]
    return Selection(
        files=[p for p in existing if _is_supported(p)],
        ignored=[p for p in existing if not _is_supported(p)],
        root=None,
    )


def extracted_root(source_file: Path) -> Path:
    """The ``Extracted`` folder beside *source_file*."""
    return source_file.parent / EXTRACTED_DIRNAME


def output_dirs(source_file: Path) -> tuple[Path, Path]:
    """Return (json_dir, markdown_dir) for *source_file*."""
    root = extracted_root(source_file)
    return root / "json", root / "markdown"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_selection.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and run the whole suite**

Run: `.venv/Scripts/ruff check .` and `.venv/Scripts/python.exe -m pytest -m "not slow"`
Expected: clean, all PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/selection.py tests/test_ui_selection.py
git commit -m "Add the selection scan and Extracted/ output paths

Reports the ignored files as well as the supported ones, so the page can
answer 'where is my .doc file?' before the user has to ask. The folder scan
is deliberately not recursive."
```

---

### Task 3: The conversion job

**Files:**
- Create: `ui/jobs.py`
- Test: `tests/test_ui_jobs.py`

**Interfaces:**
- Consumes: `main.process_file`, `extractor.limits.check_file_size`/`FileTooLargeError`, `extractor.ocr.config.configure`, `ui.selection.output_dirs`/`extracted_root`, `ui.quality.assess`/`FAIL`.
- Produces:
  - `start(files: list[Path], *, max_file_mb: int, min_confidence: float) -> str` (job id)
  - `status(job_id: str) -> dict | None`
  - `request_stop(job_id: str) -> bool`
  - `result_path(job_id: str, index: int, kind: str) -> Path | None` where `kind` is `"folder"` or `"markdown"`
  - `wait(job_id: str, timeout: float = 30.0) -> None` (test helper; blocks until the worker thread ends)

**Status dict shape** — every later task depends on these exact keys:

```python
{
  "state": "running" | "done" | "stopped",
  "done_files": int, "total_files": int,
  "done_bytes": int, "total_bytes": int,
  "current": str | None,            # filename in flight
  "current_elapsed": float,         # seconds on the current file
  "elapsed": float,                 # seconds since the run started
  "eta": float | None,              # seconds, None until the first file finishes
  "counts": {"pass": int, "review": int, "fail": int},
  "report_dir": str | None,         # set when the run ends
  "results": [ {
      "index": int, "filename": str, "source": str, "output_dir": str,
      "verdict": "pass" | "review" | "fail",
      "duration": float,
      "stats": dict | None,         # None when the file failed outright
      "notes": [str],
      "preview": str,
      "error": str | None,
  } ]
}
```

Note: `verdict` is `"fail"` for a file that raised or was too large, so the page has one field to sort and colour by. `stats` is `None` in that case, and `error` carries the sentence.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_jobs.py`:

```python
"""The conversion worker: progress, durations, isolation, stop."""
import time
from pathlib import Path

import pytest

from ui import jobs, quality


@pytest.fixture(autouse=True)
def clear_jobs():
    """Each test starts with an empty job table."""
    jobs.reset()
    yield
    jobs.reset()


def make_source(tmp_path: Path, name: str, size: int = 10) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def model_with_text(filename: str, text: str = "body"):
    return {
        "document": {"filename": filename, "source_type": "pdf", "pages": 1,
                     "author": "", "title": "", "has_images": False,
                     "image_count": 0, "schema_version": "2.0"},
        "pages": [{"unit": 1, "unit_type": "page", "has_images": False,
                   "image_count": 0,
                   "content": [{"type": "text", "content": text}]}],
    }


def stub_process(**by_name):
    """Build a process_file replacement driven by a {filename: model|Exception} map."""
    def _process(path, json_dir, md_dir):
        json_dir.mkdir(parents=True, exist_ok=True)
        md_dir.mkdir(parents=True, exist_ok=True)
        outcome = by_name.get(path.name, model_with_text(path.name))
        if isinstance(outcome, Exception):
            raise outcome
        return json_dir / f"{path.stem}.json", md_dir / f"{path.stem}.md", outcome
    return _process


def run_job(monkeypatch, files, process, **kwargs):
    """Start a job with a stubbed pipeline and wait for it to finish."""
    monkeypatch.setattr(jobs, "process_file", process)
    monkeypatch.setattr(jobs, "write_report", lambda *a, **k: None)
    job_id = jobs.start(files, max_file_mb=kwargs.get("max_file_mb", 50),
                        min_confidence=kwargs.get("min_confidence", 0.6))
    jobs.wait(job_id)
    return job_id


def test_processes_every_file_and_reports_done(monkeypatch, tmp_path):
    files = [make_source(tmp_path, "a.pdf"), make_source(tmp_path, "b.pdf")]
    job_id = run_job(monkeypatch, files, stub_process())
    state = jobs.status(job_id)
    assert state["state"] == "done"
    assert state["done_files"] == 2
    assert [r["filename"] for r in state["results"]] == ["a.pdf", "b.pdf"]


def test_progress_is_measured_in_bytes_not_file_count(monkeypatch, tmp_path):
    """A 13-small-files-plus-one-huge-scan folder must not sit at 93%."""
    files = [make_source(tmp_path, "small.pdf", size=10),
             make_source(tmp_path, "big.pdf", size=990)]
    job_id = run_job(monkeypatch, files, stub_process())
    state = jobs.status(job_id)
    assert state["total_bytes"] == 1000
    assert state["done_bytes"] == 1000


def test_one_failing_file_does_not_stop_the_run(monkeypatch, tmp_path):
    files = [make_source(tmp_path, "a.pdf"),
             make_source(tmp_path, "bad.pdf"),
             make_source(tmp_path, "c.pdf")]
    process = stub_process(**{"bad.pdf": ValueError("corrupt xref table")})
    job_id = run_job(monkeypatch, files, process)
    results = jobs.status(job_id)["results"]
    assert [r["verdict"] for r in results] == ["pass", "fail", "pass"]
    assert results[1]["error"] == "corrupt xref table"
    assert results[1]["stats"] is None


def test_oversized_file_is_recorded_as_failed_without_being_read(monkeypatch, tmp_path):
    files = [make_source(tmp_path, "huge.pdf", size=4096)]

    def never_called(*args, **kwargs):
        raise AssertionError("process_file must not run on an oversized file")

    monkeypatch.setattr(jobs, "process_file", never_called)
    monkeypatch.setattr(jobs, "write_report", lambda *a, **k: None)
    job_id = jobs.start(files, max_file_mb=0, min_confidence=0.6)
    jobs.wait(job_id)
    result = jobs.status(job_id)["results"][0]
    assert result["verdict"] == quality.FAIL
    assert "exceeds limit" in result["error"]


def test_records_a_duration_for_each_file(monkeypatch, tmp_path):
    files = [make_source(tmp_path, "a.pdf")]
    job_id = run_job(monkeypatch, files, stub_process())
    assert jobs.status(job_id)["results"][0]["duration"] >= 0.0


def test_counts_verdicts_for_the_headline(monkeypatch, tmp_path):
    files = [make_source(tmp_path, "good.pdf"), make_source(tmp_path, "bad.pdf")]
    process = stub_process(**{"bad.pdf": ValueError("boom")})
    job_id = run_job(monkeypatch, files, process)
    assert jobs.status(job_id)["counts"] == {"pass": 1, "review": 0, "fail": 1}


def test_output_lands_beside_each_source_file(monkeypatch, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    files = [make_source(folder, "a.pdf")]
    job_id = run_job(monkeypatch, files, stub_process())
    result = jobs.status(job_id)["results"][0]
    assert Path(result["output_dir"]) == folder / "Extracted"


def test_stop_ends_the_run_after_the_current_file(monkeypatch, tmp_path):
    files = [make_source(tmp_path, f"{i}.pdf") for i in range(5)]
    started: list[str] = []

    def slow_process(path, json_dir, md_dir):
        started.append(path.name)
        time.sleep(0.05)
        json_dir.mkdir(parents=True, exist_ok=True)
        md_dir.mkdir(parents=True, exist_ok=True)
        return (json_dir / "x.json", md_dir / "x.md", model_with_text(path.name))

    monkeypatch.setattr(jobs, "process_file", slow_process)
    monkeypatch.setattr(jobs, "write_report", lambda *a, **k: None)
    job_id = jobs.start(files, max_file_mb=50, min_confidence=0.6)
    time.sleep(0.06)
    jobs.request_stop(job_id)
    jobs.wait(job_id)
    state = jobs.status(job_id)
    assert state["state"] == "stopped"
    assert len(started) < 5


def test_empty_extraction_is_reported_as_failed(monkeypatch, tmp_path):
    """quality.py's rule must reach the result row, not just the unit test."""
    files = [make_source(tmp_path, "blank.pdf")]
    empty = model_with_text("blank.pdf", text="")
    empty["pages"][0]["content"] = []
    job_id = run_job(monkeypatch, files, stub_process(**{"blank.pdf": empty}))
    assert jobs.status(job_id)["results"][0]["verdict"] == quality.FAIL


def test_status_of_an_unknown_job_is_none():
    assert jobs.status("no-such-job") is None


def test_result_path_refuses_an_index_outside_the_run(monkeypatch, tmp_path):
    files = [make_source(tmp_path, "a.pdf")]
    job_id = run_job(monkeypatch, files, stub_process())
    assert jobs.result_path(job_id, 0, "folder") is not None
    assert jobs.result_path(job_id, 99, "folder") is None
    assert jobs.result_path(job_id, -1, "folder") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_jobs.py -v`
Expected: `ModuleNotFoundError: No module named 'ui.jobs'`.

- [ ] **Step 3: Implement `ui/jobs.py`**

`write_report` does not exist yet — Task 4 creates it. Import it lazily inside `_finish` so this task's tests can run and so the module import order stays simple. The tests monkeypatch `jobs.write_report`, so define it as a module-level name now.

```python
"""One conversion run on a worker thread.

The per-file sequence — size guard, process, record, continue on failure — is
the same one ``main.py`` runs, because it calls the same ``process_file``.
Nothing here re-implements extraction; if it ever needs to, that is a bug.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from pathlib import Path

from extractor.limits import FileTooLargeError, check_file_size
from extractor.ocr.config import configure
from main import process_file
from ui import quality
from ui.report import write_report
from ui.selection import extracted_root, output_dirs

logger = logging.getLogger(__name__)

_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def reset() -> None:
    """Forget every job — used between test cases."""
    with _lock:
        _jobs.clear()


class Job:
    """State for one run. Every read and write of it holds ``self._lock``."""

    def __init__(self, files: list[Path], *, max_file_mb: int,
                 min_confidence: float) -> None:
        self.files = files
        self.max_bytes = max_file_mb * 1024 * 1024
        self.min_confidence = min_confidence
        self.total_bytes = sum(self._size(path) for path in files)
        self.done_bytes = 0
        self.results: list[dict] = []
        self.state = "running"
        self.current: str | None = None
        self.current_started = 0.0
        self.started = time.monotonic()
        self.report_dir: Path | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    # --- worker -----------------------------------------------------------

    def _run(self) -> None:
        configure(min_confidence=self.min_confidence)
        for index, path in enumerate(self.files):
            if self._stop.is_set():
                break
            with self._lock:
                self.current = path.name
                self.current_started = time.monotonic()
            self.results.append(self._process_one(index, path))
            with self._lock:
                self.done_bytes += self._size(path)
                self.current = None
        self._finish()

    def _process_one(self, index: int, path: Path) -> dict:
        """Convert one file. Never raises — a bad file must not end the run."""
        started = time.monotonic()
        row = {
            "index": index,
            "filename": path.name,
            "source": str(path),
            "output_dir": str(extracted_root(path)),
            "verdict": quality.FAIL,
            "duration": 0.0,
            "stats": None,
            "notes": [],
            "preview": "",
            "error": None,
        }
        try:
            check_file_size(path, max_bytes=self.max_bytes)
            json_dir, md_dir = output_dirs(path)
            _json_path, md_path, model = process_file(path, json_dir, md_dir)
            assessment = quality.assess(model, min_confidence=self.min_confidence)
            row.update(assessment)
            row["markdown"] = str(md_path)
        except FileTooLargeError as exc:
            row["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - one bad file must not end the run
            logger.error("Failed to process %s: %s", path.name, exc)
            row["error"] = str(exc)
        row["duration"] = round(time.monotonic() - started, 2)
        return row

    def _finish(self) -> None:
        with self._lock:
            self.state = "stopped" if self._stop.is_set() else "done"
            self.current = None
        roots = [Path(row["output_dir"]) for row in self.results]
        if roots:
            self.report_dir = roots[0]
            try:
                write_report(self.report_dir, self.results, self.snapshot())
            except OSError as exc:
                logger.error("Could not write the run report: %s", exc)

    # --- reads ------------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        """The status dict the page polls."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.started
            counts = {quality.PASS: 0, quality.REVIEW: 0, quality.FAIL: 0}
            for row in self.results:
                counts[row["verdict"]] += 1
            return {
                "state": self.state,
                "done_files": len(self.results),
                "total_files": len(self.files),
                "done_bytes": self.done_bytes,
                "total_bytes": self.total_bytes,
                "current": self.current,
                "current_elapsed": (
                    round(now - self.current_started, 1) if self.current else 0.0
                ),
                "elapsed": round(elapsed, 1),
                "eta": self._eta(elapsed),
                "counts": counts,
                "report_dir": str(self.report_dir) if self.report_dir else None,
                "results": list(self.results),
            }

    def _eta(self, elapsed: float) -> float | None:
        """Seconds left, from bytes per second so far.

        None until the first file completes: an estimate made from no data is
        worse than no estimate.
        """
        if not self.results or self.done_bytes <= 0 or self.state != "running":
            return None
        remaining = self.total_bytes - self.done_bytes
        if remaining <= 0:
            return None
        return round(remaining / (self.done_bytes / elapsed), 0)


def start(files: list[Path], *, max_file_mb: int, min_confidence: float) -> str:
    """Begin a run and return its id."""
    job = Job(files, max_file_mb=max_file_mb, min_confidence=min_confidence)
    job_id = secrets.token_urlsafe(8)
    with _lock:
        _jobs[job_id] = job
    job.thread.start()
    return job_id


def status(job_id: str) -> dict | None:
    """Current progress, or None when the id is unknown."""
    with _lock:
        job = _jobs.get(job_id)
    return job.snapshot() if job else None


def request_stop(job_id: str) -> bool:
    """Ask a run to end after the file in flight. True when the id was known."""
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return False
    job.request_stop()
    return True


def result_path(job_id: str, index: int, kind: str) -> Path | None:
    """Resolve one result row to a path on disk, or None.

    Indexes, never paths: the browser must not be able to name a file for the
    shell to open.
    """
    with _lock:
        job = _jobs.get(job_id)
    if job is None or index < 0 or index >= len(job.results):
        return None
    row = job.results[index]
    target = row["output_dir"] if kind == "folder" else row.get("markdown")
    return Path(target) if target else None


def wait(job_id: str, timeout: float = 30.0) -> None:
    """Block until the run's thread ends — for tests only."""
    with _lock:
        job = _jobs.get(job_id)
    if job is not None:
        job.thread.join(timeout)
```

- [ ] **Step 4: Create a placeholder-free `ui/report.py` stub so the import resolves**

Task 4 fills this in and tests it. Create `ui/report.py` now with the real signature and a working minimal body:

```python
"""Run report written beside the output. Filled in by the next task."""
from __future__ import annotations

from pathlib import Path


def write_report(report_dir: Path, results: list[dict], summary: dict) -> None:
    """Write report.md and report.json into *report_dir*."""
    report_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_jobs.py -v`
Expected: all PASS.

If `test_stop_ends_the_run_after_the_current_file` is flaky on a fast machine, the sleep values are the knob — raise the per-file sleep, not the assertion.

- [ ] **Step 6: Lint and run the whole suite**

Run: `.venv/Scripts/ruff check .` and `.venv/Scripts/python.exe -m pytest -m "not slow"`
Expected: clean, all PASS.

- [ ] **Step 7: Commit**

```bash
git add ui/jobs.py ui/report.py tests/test_ui_jobs.py
git commit -m "Add the conversion worker with byte progress and per-file durations

Progress counts bytes, not files: a folder of small Word files and one large
scan would otherwise reach 93% in two seconds and then appear frozen. Stop
finishes the file in flight rather than killing the thread mid-write."
```

---

### Task 4: The run report

**Files:**
- Modify: `ui/report.py` (replace the stub from Task 3)
- Test: `tests/test_ui_report.py`

**Interfaces:**
- Consumes: the result rows and the status snapshot from Task 3.
- Produces: `write_report(report_dir: Path, results: list[dict], summary: dict) -> None`, writing `report_dir/report.md` and `report_dir/report.json`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_report.py`:

```python
"""The report written beside the output."""
import json

from ui.report import write_report


def row(index, filename, verdict, **extra):
    base = {
        "index": index, "filename": filename, "source": f"C:/docs/{filename}",
        "output_dir": "C:/docs/Extracted", "verdict": verdict, "duration": 1.5,
        "stats": {"units": 2, "characters": 120, "table_cells": 0, "images": 0,
                  "text_blocks": 2, "table_blocks": 0, "ocr_pages": 0,
                  "ocr_confidence": None, "est_tokens": 30},
        "notes": [], "preview": "some text", "error": None,
    }
    base.update(extra)
    return base


def summary(results):
    counts = {"pass": 0, "review": 0, "fail": 0}
    for r in results:
        counts[r["verdict"]] += 1
    return {"state": "done", "elapsed": 12.5, "counts": counts,
            "total_files": len(results), "done_files": len(results)}


def test_writes_both_files(tmp_path):
    results = [row(0, "a.pdf", "pass")]
    write_report(tmp_path, results, summary(results))
    assert (tmp_path / "report.md").is_file()
    assert (tmp_path / "report.json").is_file()


def test_creates_the_folder_when_it_is_missing(tmp_path):
    target = tmp_path / "Extracted"
    results = [row(0, "a.pdf", "pass")]
    write_report(target, results, summary(results))
    assert (target / "report.md").is_file()


def test_json_carries_the_rows_and_the_summary(tmp_path):
    results = [row(0, "a.pdf", "pass"), row(1, "b.pdf", "fail", error="corrupt")]
    write_report(tmp_path, results, summary(results))
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["summary"]["counts"] == {"pass": 1, "review": 0, "fail": 1}
    assert [f["filename"] for f in data["files"]] == ["a.pdf", "b.pdf"]
    assert data["files"][1]["error"] == "corrupt"


def test_json_documents_the_token_estimate_so_it_is_not_read_as_exact(tmp_path):
    results = [row(0, "a.pdf", "pass")]
    write_report(tmp_path, results, summary(results))
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert "characters / 4" in data["summary"]["est_tokens_note"]


def test_markdown_leads_with_the_headline(tmp_path):
    results = [row(0, "a.pdf", "pass"), row(1, "b.pdf", "review"),
               row(2, "c.pdf", "fail", error="corrupt")]
    write_report(tmp_path, results, summary(results))
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "3 files: 1 clean, 1 need review, 1 failed" in text


def test_markdown_lists_problem_files_before_clean_ones(tmp_path):
    results = [row(0, "clean.pdf", "pass"), row(1, "broken.pdf", "fail",
                                                error="corrupt")]
    write_report(tmp_path, results, summary(results))
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert text.index("broken.pdf") < text.index("clean.pdf")


def test_markdown_includes_notes_for_a_reviewed_file(tmp_path):
    results = [row(0, "scan.pdf", "review",
                   notes=["Page 1: text recovered by OCR (confidence 88%)"])]
    write_report(tmp_path, results, summary(results))
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "confidence 88%" in text


def test_handles_a_failed_row_with_no_stats(tmp_path):
    results = [row(0, "bad.pdf", "fail", stats=None, error="corrupt xref")]
    write_report(tmp_path, results, summary(results))
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "corrupt xref" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_report.py -v`
Expected: FAIL — the stub writes no files.

- [ ] **Step 3: Implement `ui/report.py`**

```python
"""The run report, written into the Extracted folder.

Two readers. The user's own LLM reads ``report.json`` to decide which files
to re-check before ingesting them. A person reads ``report.md`` — and can
forward one file that holds the whole picture instead of describing a screen
from memory.
"""
from __future__ import annotations

import json
from pathlib import Path

from ui.quality import CHARS_PER_TOKEN, FAIL, PASS, REVIEW

_VERDICT_MARK = {PASS: "OK", REVIEW: "REVIEW", FAIL: "FAILED"}
# Problem rows first: a list sorted by name hides exactly the rows that matter.
_VERDICT_ORDER = {FAIL: 0, REVIEW: 1, PASS: 2}


def write_report(report_dir: Path, results: list[dict], summary: dict) -> None:
    """Write report.md and report.json into *report_dir*."""
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            **summary,
            "est_tokens_note": (
                f"est_tokens is approximate: characters / {CHARS_PER_TOKEN}"
            ),
        },
        "files": results,
    }
    (report_dir / "report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (report_dir / "report.md").write_text(
        _render_markdown(results, summary), encoding="utf-8"
    )


def headline(counts: dict, total: int) -> str:
    """One sentence a non-technical reader can stop at."""
    return (
        f"{total} files: {counts.get(PASS, 0)} clean, "
        f"{counts.get(REVIEW, 0)} need review, {counts.get(FAIL, 0)} failed"
    )


def _render_markdown(results: list[dict], summary: dict) -> str:
    counts = summary.get("counts", {})
    lines = [
        "# Extraction report",
        "",
        headline(counts, summary.get("total_files", len(results))),
        "",
        f"Total time: {summary.get('elapsed', 0):.1f} s",
        "",
    ]
    if summary.get("state") == "stopped":
        lines += ["The run was stopped early — files after the last one listed "
                  "below were not processed.", ""]

    for row in sorted(results, key=lambda r: (_VERDICT_ORDER[r["verdict"]],
                                              r["filename"])):
        lines += _render_row(row)

    lines += ["", "---", "",
              f"Token counts are approximate: characters / {CHARS_PER_TOKEN}."]
    return "\n".join(lines) + "\n"


def _render_row(row: dict) -> list[str]:
    lines = [f"## {_VERDICT_MARK[row['verdict']]} — {row['filename']}", ""]
    lines.append(f"- Time: {row['duration']:.1f} s")
    if row["error"]:
        lines.append(f"- Error: {row['error']}")
    stats = row["stats"]
    if stats:
        lines.append(
            f"- Extracted: {stats['characters']} characters, "
            f"{stats['units']} unit(s), {stats['table_blocks']} table(s), "
            f"{stats['images']} image(s), ~{stats['est_tokens']} tokens"
        )
        if stats["ocr_pages"]:
            confidence = stats["ocr_confidence"]
            shown = f"{confidence * 100:.0f}%" if confidence is not None else "n/a"
            lines.append(
                f"- OCR: {stats['ocr_pages']} page(s), average confidence {shown}"
            )
    for note in row["notes"]:
        lines.append(f"- {note}")
    if row["preview"]:
        lines += ["", "> " + row["preview"].replace("\n", " ")]
    lines.append("")
    return lines
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_report.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and run the whole suite**

Run: `.venv/Scripts/ruff check .` and `.venv/Scripts/python.exe -m pytest -m "not slow"`
Expected: clean, all PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/report.py tests/test_ui_report.py
git commit -m "Write a run report beside the output

report.json lets the user's own LLM decide which files to re-check before
ingesting them; report.md lets them forward one file instead of describing a
screen. Problem files are listed first in both."
```

---

### Task 5: Native Windows pickers

**Files:**
- Create: `ui/picker.py`
- Test: `tests/test_ui_picker.py`

**Interfaces:**
- Produces:
  - `pick_folder() -> Path | None`
  - `pick_files() -> list[Path]`
  - `_run_dialog(mode: str) -> list[str]` — the seam the tests replace

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_picker.py`:

```python
"""Native dialogs, with the subprocess replaced."""
from pathlib import Path

from ui import picker


def test_pick_folder_returns_the_chosen_path(monkeypatch):
    monkeypatch.setattr(picker, "_run_dialog", lambda mode: ["C:/docs"])
    assert picker.pick_folder() == Path("C:/docs")


def test_pick_folder_returns_none_when_cancelled(monkeypatch):
    monkeypatch.setattr(picker, "_run_dialog", lambda mode: [])
    assert picker.pick_folder() is None


def test_pick_files_returns_every_chosen_path(monkeypatch):
    monkeypatch.setattr(picker, "_run_dialog",
                        lambda mode: ["C:/docs/a.pdf", "C:/docs/b.docx"])
    assert picker.pick_files() == [Path("C:/docs/a.pdf"), Path("C:/docs/b.docx")]


def test_pick_files_returns_empty_when_cancelled(monkeypatch):
    monkeypatch.setattr(picker, "_run_dialog", lambda mode: [])
    assert picker.pick_files() == []


def test_blank_lines_from_the_dialog_are_dropped(monkeypatch):
    """A cancelled folder dialog prints an empty line, not nothing."""
    monkeypatch.setattr(picker, "_run_dialog", lambda mode: ["", "  "])
    assert picker.pick_folder() is None
    assert picker.pick_files() == []


def test_a_crashed_dialog_reads_as_a_cancel(monkeypatch):
    def boom(mode):
        raise OSError("tkinter is not available")

    monkeypatch.setattr(picker, "_run_dialog", boom)
    assert picker.pick_folder() is None
    assert picker.pick_files() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_picker.py -v`
Expected: `ModuleNotFoundError: No module named 'ui.picker'`.

- [ ] **Step 3: Implement `ui/picker.py`**

```python
"""Native Windows folder and file dialogs.

Each dialog runs in a short-lived subprocess. That is not an optimization to
remove later: tkinter must own the main thread, and the HTTP server already
owns it. Out of process the server stays responsive, a crashed dialog cannot
take the app down, and the whole thing is stubbable in tests.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Timeout is generous on purpose: the user may leave the dialog open.
DIALOG_TIMEOUT_S = 900

_DIALOG_SCRIPT = """
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
if sys.argv[1] == "folder":
    print(filedialog.askdirectory(title="Choose a folder with documents") or "")
else:
    for path in filedialog.askopenfilenames(title="Choose documents"):
        print(path)
root.destroy()
"""


def _run_dialog(mode: str) -> list[str]:
    """Open one dialog out of process and return the lines it printed."""
    completed = subprocess.run(
        [sys.executable, "-c", _DIALOG_SCRIPT, mode],
        capture_output=True, text=True, timeout=DIALOG_TIMEOUT_S, check=False,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "the file dialog failed to open")
    return completed.stdout.splitlines()


def _selected_paths(mode: str) -> list[Path]:
    """Run a dialog and return the non-blank paths, or nothing on any failure.

    A dialog that cannot open is reported as a cancel: the user gets an
    unchanged page rather than an error they cannot act on, and the log keeps
    the reason.
    """
    try:
        lines = _run_dialog(mode)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("Could not open the %s dialog: %s", mode, exc)
        return []
    return [Path(line.strip()) for line in lines if line.strip()]


def pick_folder() -> Path | None:
    """Ask for one folder. None when the user cancels."""
    paths = _selected_paths("folder")
    return paths[0] if paths else None


def pick_files() -> list[Path]:
    """Ask for one or more files. Empty when the user cancels."""
    return _selected_paths("files")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_picker.py -v`
Expected: all PASS.

- [ ] **Step 5: Verify the real dialog opens once, by hand**

Run: `.venv/Scripts/python.exe -c "from ui.picker import pick_folder; print(pick_folder())"`
Expected: a Windows folder dialog appears in front of other windows; choosing a folder prints its path, cancelling prints `None`.

- [ ] **Step 6: Lint and run the whole suite**

Run: `.venv/Scripts/ruff check .` and `.venv/Scripts/python.exe -m pytest -m "not slow"`
Expected: clean, all PASS.

- [ ] **Step 7: Commit**

```bash
git add ui/picker.py tests/test_ui_picker.py
git commit -m "Add native Windows folder and file dialogs

Dialogs run out of process because tkinter must own the main thread and the
HTTP server already does. A dialog that cannot open reads as a cancel, so a
missing tkinter never leaves the user at an error they cannot act on."
```

---

### Task 6: The HTTP server

**Files:**
- Create: `ui/server.py`
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `TOKEN: str` (module-level, regenerated by `reset_state()`)
  - `handle(method: str, path: str, query: dict, body: dict) -> tuple[int, dict]` — the pure route dispatcher every test drives
  - `reset_state() -> None`
  - `serve(port: int = 0, open_browser: bool = True) -> None`
  - `main() -> None`

Splitting the route table (`handle`) from the `BaseHTTPRequestHandler` plumbing is what makes the routes testable without sockets. The handler class does nothing but parse a request, call `handle`, and write JSON back.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_server.py`:

```python
"""Route table, token check, and path safety — no sockets involved."""
from pathlib import Path

import pytest

from ui import jobs, server


@pytest.fixture(autouse=True)
def fresh_server():
    server.reset_state()
    jobs.reset()
    yield
    jobs.reset()


def call(method, path, body=None, *, token=None):
    query = {"t": [token if token is not None else server.TOKEN]}
    return server.handle(method, path, query, body or {})


def make_files(root: Path, *names):
    for name in names:
        (root / name).write_bytes(b"x" * 10)


# --- authentication -------------------------------------------------------

def test_api_rejects_a_request_with_no_token():
    status, _body = server.handle("GET", "/api/env", {}, {})
    assert status == 403


def test_api_rejects_a_wrong_token():
    status, _body = call("GET", "/api/env", token="not-the-token")
    assert status == 403


def test_api_accepts_the_session_token():
    status, body = call("GET", "/api/env")
    assert status == 200
    assert "ocr" in body


def test_the_token_changes_between_sessions():
    first = server.TOKEN
    server.reset_state()
    assert server.TOKEN != first


# --- selection ------------------------------------------------------------

def test_scan_folder_reports_supported_and_ignored(tmp_path, monkeypatch):
    make_files(tmp_path, "a.pdf", "b.doc")
    monkeypatch.setattr(server.picker, "pick_folder", lambda: tmp_path)
    status, body = call("POST", "/api/pick-folder")
    assert status == 200
    assert body["file_count"] == 1
    assert body["ignored_count"] == 1
    assert body["ignored_extensions"] == [".doc"]
    assert body["path"] == str(tmp_path)


def test_cancelled_folder_dialog_returns_a_cancelled_flag(monkeypatch):
    monkeypatch.setattr(server.picker, "pick_folder", lambda: None)
    status, body = call("POST", "/api/pick-folder")
    assert status == 200
    assert body["cancelled"] is True


def test_pick_files_reports_the_same_shape(tmp_path, monkeypatch):
    make_files(tmp_path, "a.pdf", "b.doc")
    monkeypatch.setattr(server.picker, "pick_files",
                        lambda: [tmp_path / "a.pdf", tmp_path / "b.doc"])
    status, body = call("POST", "/api/pick-files")
    assert status == 200
    assert body["file_count"] == 1
    assert body["ignored_extensions"] == [".doc"]


# --- convert --------------------------------------------------------------

def test_convert_without_a_selection_is_rejected():
    status, body = call("POST", "/api/convert")
    assert status == 400
    assert "error" in body


def test_convert_with_zero_supported_files_is_rejected(tmp_path, monkeypatch):
    make_files(tmp_path, "only.doc")
    monkeypatch.setattr(server.picker, "pick_folder", lambda: tmp_path)
    call("POST", "/api/pick-folder")
    status, body = call("POST", "/api/convert")
    assert status == 400


def test_convert_starts_a_job_and_status_reports_it(tmp_path, monkeypatch):
    make_files(tmp_path, "a.pdf")
    monkeypatch.setattr(server.picker, "pick_folder", lambda: tmp_path)
    call("POST", "/api/pick-folder")
    started = {}
    monkeypatch.setattr(server.jobs, "start",
                        lambda files, **kw: started.setdefault("id", "job-1"))
    monkeypatch.setattr(server.jobs, "status",
                        lambda job_id: {"state": "done", "results": []})
    status, body = call("POST", "/api/convert")
    assert status == 200
    assert body["job_id"] == "job-1"
    status, body = call("GET", "/api/status", {"job": "job-1"})
    assert status == 200
    assert body["state"] == "done"


def test_status_of_an_unknown_job_is_404(monkeypatch):
    monkeypatch.setattr(server.jobs, "status", lambda job_id: None)
    status, _body = call("GET", "/api/status", {"job": "nope"})
    assert status == 404


def test_stop_forwards_to_the_job(monkeypatch):
    seen = {}
    monkeypatch.setattr(server.jobs, "request_stop",
                        lambda job_id: seen.setdefault("id", job_id) or True)
    status, _body = call("POST", "/api/stop", {"job": "job-1"})
    assert status == 200
    assert seen["id"] == "job-1"


# --- path safety ----------------------------------------------------------

def test_open_refuses_an_index_outside_the_run(monkeypatch):
    monkeypatch.setattr(server.jobs, "result_path", lambda *a: None)
    status, _body = call("POST", "/api/open-folder", {"job": "job-1", "index": 99})
    assert status == 404


def test_open_never_takes_a_path_from_the_request(monkeypatch, tmp_path):
    """The browser names an index; the server resolves the path."""
    opened = {}
    monkeypatch.setattr(server, "_reveal", lambda path: opened.setdefault("p", path))
    monkeypatch.setattr(server.jobs, "result_path", lambda *a: tmp_path)
    status, _body = call("POST", "/api/open-folder",
                         {"job": "job-1", "index": 0, "path": "C:/Windows"})
    assert status == 200
    assert opened["p"] == tmp_path


def test_unknown_route_is_404():
    status, _body = call("GET", "/api/nonsense")
    assert status == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_server.py -v`
Expected: `ModuleNotFoundError: No module named 'ui.server'`.

- [ ] **Step 3: Implement `ui/server.py`**

```python
"""Loopback HTTP server for the local UI.

The route table is a plain function over (method, path, query, body) so it can
be tested without a socket; the request handler below does nothing but parse,
call it, and write JSON back.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from extractor.limits import MAX_FILE_MB
from extractor.ocr import registry
from extractor.ocr.config import DEFAULT_MIN_CONFIDENCE
from ui import jobs, picker, selection

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
IDLE_TIMEOUT_S = 30 * 60

TOKEN = secrets.token_urlsafe(16)
_selection: selection.Selection | None = None
_shutdown = threading.Event()
_last_request = threading.Event()


def reset_state() -> None:
    """New session token, no selection — also used between test cases."""
    global TOKEN, _selection
    TOKEN = secrets.token_urlsafe(16)
    _selection = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def handle(method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    """Dispatch one API call. Returns (status_code, json_body)."""
    if query.get("t", [None])[0] != TOKEN:
        return 403, {"error": "This page is no longer valid. Restart the app."}

    route = (method, path)
    if route == ("GET", "/api/env"):
        return _env()
    if route == ("POST", "/api/pick-folder"):
        return _pick(picker.pick_folder, folder=True)
    if route == ("POST", "/api/pick-files"):
        return _pick(picker.pick_files, folder=False)
    if route == ("POST", "/api/convert"):
        return _convert(body)
    if route == ("GET", "/api/status"):
        return _status(query.get("job", [""])[0])
    if route == ("POST", "/api/stop"):
        return _stop(body)
    if route in (("POST", "/api/open-folder"), ("POST", "/api/open-file")):
        return _open(body, "folder" if path.endswith("folder") else "markdown")
    if route == ("POST", "/api/quit"):
        _shutdown.set()
        return 200, {"ok": True}
    return 404, {"error": "unknown route"}


def _env() -> tuple[int, dict]:
    """Whether OCR can run, and the sentence explaining it when it cannot."""
    available = registry.is_available()
    reason = registry.unavailable_reason()
    return 200, {
        "ocr": available,
        "ocr_reason": "" if available else str(reason or "OCR is not available"),
        "max_file_mb": MAX_FILE_MB,
    }


def _pick(dialog, *, folder: bool) -> tuple[int, dict]:
    """Run a dialog, scan the result, and remember it for the next convert."""
    global _selection
    chosen = dialog()
    if not chosen:
        return 200, {"cancelled": True}
    _selection = (selection.scan_folder(chosen) if folder
                  else selection.scan_files(chosen))
    return 200, {
        "cancelled": False,
        "path": str(_selection.root) if folder else f"{len(chosen)} file(s) chosen",
        "file_count": len(_selection.files),
        "ignored_count": len(_selection.ignored),
        "ignored_extensions": _selection.ignored_extensions(),
    }


def _convert(body: dict) -> tuple[int, dict]:
    """Start a run over the current selection, or over named retry sources."""
    retry = body.get("sources")
    if retry:
        files = [Path(item) for item in retry]
    elif _selection is not None:
        files = _selection.files
    else:
        return 400, {"error": "Choose a folder or some files first."}

    if not files:
        return 400, {"error": "None of the chosen files are a supported format."}

    job_id = jobs.start(
        files,
        max_file_mb=int(body.get("max_file_mb", MAX_FILE_MB)),
        min_confidence=float(body.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
    )
    return 200, {"job_id": job_id}


def _status(job_id: str) -> tuple[int, dict]:
    state = jobs.status(job_id)
    if state is None:
        return 404, {"error": "unknown job"}
    return 200, state


def _stop(body: dict) -> tuple[int, dict]:
    if not jobs.request_stop(body.get("job", "")):
        return 404, {"error": "unknown job"}
    return 200, {"ok": True}


def _open(body: dict, kind: str) -> tuple[int, dict]:
    """Open a result folder or file.

    The request names a job and an index. Any ``path`` it sends is ignored —
    the server resolves the path itself, so the browser can never hand a path
    to the shell.
    """
    target = jobs.result_path(body.get("job", ""), int(body.get("index", -1)), kind)
    if target is None or not target.exists():
        return 404, {"error": "that result is not available"}
    _reveal(target)
    return 200, {"ok": True}


def _reveal(path: Path) -> None:
    """Show *path* in Explorer, or open it with its default application."""
    if path.is_dir():
        subprocess.Popen(["explorer.exe", str(path)])  # noqa: S603,S607
    else:
        subprocess.Popen(["explorer.exe", f"/select,{path}"])  # noqa: S603,S607


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """Parses a request, calls handle(), writes JSON. No logic of its own."""

    server_version = "KnowledgeExtractorUI"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            status, body = handle("GET", parsed.path, parse_qs(parsed.query), {})
            self._send_json(status, body)
        else:
            self._send_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "malformed request"})
            return
        status, response = handle("POST", parsed.path, parse_qs(parsed.query), body)
        self._send_json(status, response)

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self.send_error(404)
            return
        payload = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         mimetypes.guess_type(target.name)[0] or "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(port: int = 0, open_browser: bool = True) -> None:
    """Run the server on loopback until /api/quit or the idle timeout."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    actual_port = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/?t={TOKEN}"
    print(f"Knowledge Extractor is running at {url}")
    print("Keep this window open. Closing it stops the app.")
    if open_browser:
        webbrowser.open(url)

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _shutdown.wait(timeout=IDLE_TIMEOUT_S)
    httpd.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    serve()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_server.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and run the whole suite**

Run: `.venv/Scripts/ruff check .` and `.venv/Scripts/python.exe -m pytest -m "not slow"`
Expected: clean, all PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/server.py tests/test_ui_server.py
git commit -m "Add the loopback HTTP server and route table

The route table is a plain function over (method, path, query, body), so
every route is tested without a socket. Open requests name a job and an
index; the server resolves the path, so the browser never hands one to the
shell."
```

---

### Task 7: The page

**Files:**
- Create: `ui/static/index.html`
- Create: `ui/static/style.css`
- Create: `ui/static/app.js`

**Interfaces:**
- Consumes: the routes and the status dict shape from Tasks 3 and 6.
- Produces: nothing other tasks import.

This task is verified by hand. There is no DOM test framework in the project and adding one would be a larger change than the page itself.

- [ ] **Step 1: Create `ui/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Knowledge Extractor</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <main>
    <h1>Knowledge Extractor</h1>
    <p class="lede">Turn documents into text your AI tools can read.</p>

    <section id="choose">
      <div class="row">
        <button id="pick-folder" class="secondary">Choose folder…</button>
        <button id="pick-files" class="secondary">Choose files…</button>
      </div>
      <p id="chosen" class="muted" hidden></p>
      <p id="ignored" class="muted small" hidden></p>
      <button id="convert" class="primary" hidden>Convert</button>
      <p id="env" class="muted small"></p>
      <p id="error" class="error" hidden></p>
    </section>

    <section id="running" hidden>
      <div class="bar"><div id="bar-fill"></div></div>
      <p id="progress-line"></p>
      <p id="current-line" class="muted"></p>
      <button id="stop" class="secondary">Stop</button>
    </section>

    <section id="finished" hidden>
      <h2 id="headline"></h2>
      <p id="totals" class="muted small"></p>
      <div class="row">
        <button id="open-results" class="primary">Open results folder</button>
        <button id="again" class="secondary">Convert something else</button>
        <button id="retry" class="secondary" hidden>Retry failed only</button>
      </div>
    </section>

    <ul id="results"></ul>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `ui/static/style.css`**

```css
/* Minimal, high whitespace, follows the reader's theme. No framework, no CDN:
   the page must render with the machine offline. */
:root {
  --bg: #fbfbfd; --panel: #ffffff; --ink: #1d1d1f; --muted: #6e6e73;
  --line: #e3e3e6; --accent: #0071e3;
  --ok: #1d7a3e; --review: #9a6400; --fail: #b3261e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161618; --panel: #202022; --ink: #f5f5f7; --muted: #9a9aa0;
    --line: #313134; --accent: #2f97ff;
    --ok: #4cc47c; --review: #e0a33a; --fail: #ff6b60;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif;
}
main { max-width: 760px; margin: 0 auto; padding: 56px 24px 96px; }
h1 { font-size: 30px; font-weight: 600; margin: 0 0 6px; letter-spacing: -0.02em; }
h2 { font-size: 20px; font-weight: 600; margin: 0 0 6px; }
.lede { color: var(--muted); margin: 0 0 32px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.error { color: var(--fail); }
.row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
button {
  font: inherit; padding: 10px 18px; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink);
  transition: opacity .15s;
}
button:hover { opacity: .82; }
button:disabled { opacity: .45; cursor: default; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
section { margin-bottom: 28px; }
.bar {
  height: 6px; background: var(--line); border-radius: 3px;
  overflow: hidden; margin-bottom: 12px;
}
#bar-fill { height: 100%; width: 0; background: var(--accent); transition: width .3s; }
#results { list-style: none; padding: 0; margin: 24px 0 0; }
#results li {
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; margin-bottom: 10px;
}
.head { display: flex; gap: 10px; align-items: baseline; }
.name { font-weight: 600; flex: 1; word-break: break-all; }
.mark { font-size: 12px; font-weight: 600; letter-spacing: .04em; }
.mark.pass { color: var(--ok); }
.mark.review { color: var(--review); }
.mark.fail { color: var(--fail); }
.note { margin: 6px 0 0; font-size: 13px; color: var(--muted); }
.preview {
  margin: 10px 0 0; padding: 8px 12px; border-left: 2px solid var(--line);
  font-size: 13px; color: var(--muted); white-space: pre-wrap;
}
details summary { cursor: pointer; font-size: 13px; color: var(--accent); margin-top: 8px; }
details table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
details td { padding: 3px 0; color: var(--muted); }
details td:last-child { text-align: right; color: var(--ink); }
```

- [ ] **Step 3: Create `ui/static/app.js`**

```js
"use strict";

// The token in the URL is what proves this page belongs to this session.
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const POLL_MS = 500;

let jobId = null;
let timer = null;
let lastResults = [];

const $ = (id) => document.getElementById(id);

async function api(method, path, body) {
  const response = await fetch(`${path}${path.includes("?") ? "&" : "?"}t=${TOKEN}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: method === "POST" ? JSON.stringify(body || {}) : undefined,
  });
  return { status: response.status, data: await response.json() };
}

function showError(message) {
  const box = $("error");
  box.textContent = message || "";
  box.hidden = !message;
}

function seconds(value) {
  if (value == null) return "";
  if (value < 90) return `${Math.round(value)} s`;
  return `about ${Math.round(value / 60)} min`;
}

// --- choosing --------------------------------------------------------------

async function choose(route) {
  showError("");
  const { data } = await api("POST", route);
  if (data.cancelled) return;
  $("chosen").textContent = `${data.path} — ${data.file_count} supported file(s) found`;
  $("chosen").hidden = false;
  if (data.ignored_count) {
    $("ignored").textContent =
      `${data.ignored_count} file(s) ignored (${data.ignored_extensions.join(", ")})`;
    $("ignored").hidden = false;
  } else {
    $("ignored").hidden = true;
  }
  $("convert").hidden = data.file_count === 0;
}

// --- converting ------------------------------------------------------------

async function convert(sources) {
  showError("");
  const { status, data } = await api("POST", "/api/convert", sources ? { sources } : {});
  if (status !== 200) {
    showError(data.error);
    return;
  }
  jobId = data.job_id;
  $("choose").hidden = true;
  $("finished").hidden = true;
  $("running").hidden = false;
  $("results").innerHTML = "";
  timer = setInterval(poll, POLL_MS);
}

async function poll() {
  const { status, data } = await api("GET", `/api/status?job=${jobId}`);
  if (status !== 200) {
    clearInterval(timer);
    return;
  }
  render(data);
  if (data.state !== "running") {
    clearInterval(timer);
    finish(data);
  }
}

function render(state) {
  const percent = state.total_bytes
    ? Math.round((state.done_bytes / state.total_bytes) * 100)
    : 0;
  $("bar-fill").style.width = `${percent}%`;
  $("progress-line").textContent =
    `File ${Math.min(state.done_files + 1, state.total_files)} of ${state.total_files}`
    + (state.eta ? ` — ${seconds(state.eta)} left` : "");
  $("current-line").textContent = state.current
    ? `Reading ${state.current} — ${state.current_elapsed.toFixed(0)} s`
    : "";
  renderResults(state.results);
}

function finish(state) {
  lastResults = state.results;
  const c = state.counts;
  $("running").hidden = true;
  $("finished").hidden = false;
  $("headline").textContent =
    `${state.total_files} files: ${c.pass} clean, ${c.review} need review, ${c.fail} failed`;
  const totals = state.results.reduce((sum, r) => {
    if (r.stats) {
      sum.characters += r.stats.characters;
      sum.tokens += r.stats.est_tokens;
      sum.units += r.stats.units;
    }
    return sum;
  }, { characters: 0, tokens: 0, units: 0 });
  $("totals").textContent =
    `${totals.characters.toLocaleString()} characters, ${totals.units} unit(s), `
    + `approx. ${totals.tokens.toLocaleString()} tokens — ${seconds(state.elapsed)}`;
  $("retry").hidden = c.fail === 0;
}

// --- results ---------------------------------------------------------------

const ORDER = { fail: 0, review: 1, pass: 2 };
const LABEL = { pass: "OK", review: "REVIEW", fail: "FAILED" };

function renderResults(results) {
  const list = $("results");
  list.innerHTML = "";
  [...results]
    .sort((a, b) => ORDER[a.verdict] - ORDER[b.verdict] ||
                    a.filename.localeCompare(b.filename))
    .forEach((row) => list.appendChild(resultRow(row)));
}

function resultRow(row) {
  const item = document.createElement("li");

  const head = document.createElement("div");
  head.className = "head";
  const mark = document.createElement("span");
  mark.className = `mark ${row.verdict}`;
  mark.textContent = LABEL[row.verdict];
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = row.filename;
  const time = document.createElement("span");
  time.className = "muted small";
  time.textContent = `${row.duration.toFixed(1)} s`;
  head.append(mark, name, time);
  item.append(head);

  if (row.error) {
    item.append(paragraph("note", row.error));
  }
  row.notes.slice(0, 3).forEach((note) => item.append(paragraph("note", note)));
  if (row.notes.length > 3) {
    item.append(paragraph("note", `+ ${row.notes.length - 3} more note(s)`));
  }
  if (row.preview) {
    item.append(paragraph("preview", row.preview));
  }
  if (row.stats) {
    item.append(statsBlock(row));
  }
  return item;
}

function paragraph(className, text) {
  const node = document.createElement("p");
  node.className = className;
  node.textContent = text;
  return node;
}

function statsBlock(row) {
  const s = row.stats;
  const rows = [
    ["Characters", s.characters.toLocaleString()],
    ["Approx. tokens", s.est_tokens.toLocaleString()],
    ["Units", s.units],
    ["Text blocks", s.text_blocks],
    ["Tables", `${s.table_blocks} (${s.table_cells} cells)`],
    ["Images", s.images],
  ];
  if (s.ocr_pages) {
    const confidence = s.ocr_confidence == null
      ? "n/a" : `${Math.round(s.ocr_confidence * 100)}%`;
    rows.push(["Pages read by OCR", `${s.ocr_pages} (avg. ${confidence})`]);
  }
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Details";
  const table = document.createElement("table");
  rows.forEach(([label, value]) => {
    const tr = table.insertRow();
    tr.insertCell().textContent = label;
    tr.insertCell().textContent = value;
  });
  details.append(summary, table);

  const open = document.createElement("button");
  open.className = "secondary";
  open.textContent = "Open Markdown";
  open.style.marginTop = "10px";
  open.onclick = () => api("POST", "/api/open-file", { job: jobId, index: row.index });
  details.append(open);
  return details;
}

// --- wiring ----------------------------------------------------------------

$("pick-folder").onclick = () => choose("/api/pick-folder");
$("pick-files").onclick = () => choose("/api/pick-files");
$("convert").onclick = () => convert(null);
$("stop").onclick = () => api("POST", "/api/stop", { job: jobId });
$("open-results").onclick = () =>
  api("POST", "/api/open-folder", { job: jobId, index: 0 });
$("again").onclick = () => {
  $("finished").hidden = true;
  $("results").innerHTML = "";
  $("choose").hidden = false;
};
$("retry").onclick = () =>
  convert(lastResults.filter((r) => r.verdict === "fail").map((r) => r.source));

// Closing the tab stops the server, so a forgotten window leaves nothing running.
window.addEventListener("pagehide", () => {
  navigator.sendBeacon(`/api/quit?t=${TOKEN}`, new Blob(["{}"],
    { type: "application/json" }));
});

api("GET", "/api/env").then(({ data }) => {
  $("env").textContent = data.ocr
    ? "Scanned pages and images will be read with OCR."
    : `OCR is not available — scanned pages and images will not be read. ${data.ocr_reason}`;
});
```

- [ ] **Step 4: Verify by hand**

Run: `.venv/Scripts/python.exe -m ui.server`

Check, in order:
1. The browser opens and the page renders. The OCR line at the bottom of the first section reflects reality (turn OCR off with a renamed `models/ocr` to see the other branch).
2. *Choose folder…* opens the real Windows dialog. Point it at `input/`. The count and any "ignored" line appear.
3. **Convert** runs. The bar advances, the current filename and its timer update, the ETA appears after the first file.
4. On completion the headline, totals, and per-file rows appear with problem rows first. Expanding **Details** shows the statistics table; **Open Markdown** opens the file.
5. **Open results folder** opens `input/Extracted`, which contains `json/`, `markdown/`, `report.md`, `report.json`.
6. Switch the OS between light and dark: the page follows.
7. Close the tab; the console process exits.

- [ ] **Step 5: Lint and run the whole suite**

Run: `.venv/Scripts/ruff check .` and `.venv/Scripts/python.exe -m pytest -m "not slow"`
Expected: clean, all PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/static
git commit -m "Add the single-page UI

Detail is layered: verdict, name, duration and one line of notes by default,
with the statistics table behind a disclosure. A non-technical user must be
able to read the headline and stop there."
```

---

### Task 8: The bootstrap

**Files:**
- Create: `Start.bat`
- Create: `launcher/setup.ps1`
- Modify: `.gitignore` (add `logs/launcher.log`)

**Interfaces:**
- Consumes: `python -m ui.server` from Task 6.
- Produces: nothing other tasks import.

Verified by hand. Do not attempt to unit test PowerShell here — the project has no harness for it, and the behaviour that matters (a clean Windows profile with no Python) cannot be simulated in pytest.

- [ ] **Step 1: Create `Start.bat`**

```bat
@echo off
rem Entry point for non-technical users. All logic lives in launcher\setup.ps1
rem so it can be read and maintained; -ExecutionPolicy Bypass applies to this
rem process only and needs no administrator rights.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher\setup.ps1"
if errorlevel 1 pause
```

- [ ] **Step 2: Create `launcher/setup.ps1`**

```powershell
# Idempotent environment bootstrap for the Knowledge Extractor UI.
# Every step checks before it acts, so a second run costs about a second.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'launcher.log'

function Write-Log($message) {
    "$(Get-Date -Format s)  $message" | Add-Content -Path $log -Encoding utf8
}

function Fail($message) {
    Write-Host ''
    Write-Host $message -ForegroundColor Red
    Write-Host "Details were written to $log"
    Write-Log "FAILED: $message"
    exit 1
}

# --- 1. Is the environment already good? ------------------------------------
if (Test-Path $venvPython) {
    & $venvPython -c "import fitz, docx, openpyxl, ui" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Log 'Environment already present.'
        Write-Host 'Starting Knowledge Extractor...'
        & $venvPython -m ui.server
        exit $LASTEXITCODE
    }
    Write-Log 'Virtual environment is incomplete; reinstalling dependencies.'
}

Write-Host 'First run — setting things up. This takes a few minutes.'
Write-Host ''

# --- 2. Find Python 3.11+ ---------------------------------------------------
function Find-Python {
    foreach ($candidate in @('py -3.13', 'py -3.12', 'py -3.11', 'python')) {
        $parts = $candidate.Split(' ')
        $exe = $parts[0]
        $args = if ($parts.Count -gt 1) { $parts[1..($parts.Count - 1)] } else { @() }
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $version = & $exe @args -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
        $parsed = [version]$version
        if ($parsed -ge [version]'3.11') {
            Write-Log "Using Python $version via '$candidate'."
            return ,@($exe, $args)
        }
    }
    return $null
}

$python = Find-Python

# --- 3. Install Python if it is missing -------------------------------------
if ($null -eq $python) {
    Write-Host 'Python is not installed. Installing it now (no admin rights needed)...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 --scope user `
            --accept-source-agreements --accept-package-agreements | Out-Null
        Write-Log 'Installed Python via winget.'
    } else {
        $installer = Join-Path $env:TEMP 'python-3.12-installer.exe'
        $url = 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe'
        try {
            Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        } catch {
            Write-Log $_.Exception.Message
            Fail 'Could not download Python. Install Python 3.12 from python.org, then run Start.bat again.'
        }
        Start-Process -FilePath $installer -Wait -ArgumentList `
            '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_tcltk=1'
        Write-Log 'Installed Python via the python.org installer.'
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $python = Find-Python
    if ($null -eq $python) {
        Fail 'Python was installed but could not be found. Restart the computer, then run Start.bat again.'
    }
}

# --- 4. Create the virtual environment --------------------------------------
if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating the local environment...'
    & $python[0] @($python[1]) -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the local Python environment.' }
}

# --- 5. Install dependencies ------------------------------------------------
Write-Host 'Installing components...'
& $venvPython -m pip install --upgrade pip *>> $log
& $venvPython -m pip install -e ".[ocr]" *>> $log
if ($LASTEXITCODE -ne 0) {
    # A machine that cannot install onnxruntime should still convert Word and Excel.
    Write-Host 'OCR components could not be installed — continuing without OCR.' `
        -ForegroundColor Yellow
    Write-Log 'OCR extra failed; falling back to the base install.'
    & $venvPython -m pip install -e . *>> $log
    if ($LASTEXITCODE -ne 0) { Fail 'Could not install the required components.' }
}

# --- 6. Check the OCR models ------------------------------------------------
$models = Get-ChildItem -Path (Join-Path $root 'models\ocr') -Filter '*.onnx' `
    -ErrorAction SilentlyContinue
if (-not $models) {
    Write-Host 'OCR model files are missing — scanned pages will not be read.' `
        -ForegroundColor Yellow
    Write-Log 'No .onnx files under models/ocr.'
}

# --- 7. Launch --------------------------------------------------------------
Write-Host ''
Write-Host 'Setup complete. Starting Knowledge Extractor...'
& $venvPython -m ui.server
```

- [ ] **Step 3: Add the launcher log to `.gitignore`**

Append to `.gitignore`:

```
logs/launcher.log
```

- [ ] **Step 4: Verify the warm path**

With the venv already present, double-click `Start.bat`.
Expected: the browser opens within a couple of seconds, no install output.

- [ ] **Step 5: Verify the cold path**

On a clean Windows profile with no Python (a fresh VM or a spare user account):
1. Copy the repo folder there and double-click `Start.bat`.
2. Expected: it reports it is installing, installs Python, creates the venv, installs components, and opens the page.
3. Double-click it a second time. Expected: straight to the page in about a second.
4. Check `logs/launcher.log` holds the detail and the console held none of it.

If a clean profile is not available, test the branch by temporarily renaming `.venv` and confirming steps 4–7 re-run correctly. Record in the commit message which of the two you did — do not claim the cold path was verified if it was not.

- [ ] **Step 6: Commit**

```bash
git add Start.bat launcher/setup.ps1 .gitignore
git commit -m "Add the double-click launcher

Every bootstrap step checks before it acts, so a second run costs about a
second. A machine that cannot install onnxruntime still converts Word and
Excel rather than failing outright, and every failure path ends in a sentence
a non-technical user can act on."
```

---

### Task 9: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`

**Interfaces:** none.

- [ ] **Step 1: Add the user-facing section to `README.md`**

Insert immediately after the project title and one-line description, before the existing setup instructions:

```markdown
## Quick start (no setup)

1. Double-click **`Start.bat`**.
   The first run installs what it needs and takes a few minutes. Later runs
   open in about a second.
2. Click **Choose folder…** and point it at your documents.
3. Click **Convert**.
4. Click **Open results folder**. Your files are in `Extracted/markdown`
   (for reading and for feeding to an AI tool) and `Extracted/json`
   (structured, for building on).

Each file gets a verdict: **OK**, **REVIEW** (it worked, but check it), or
**FAILED**. `Extracted/report.md` holds the same summary as a file you can
keep or forward.

Everything runs on your own computer. Nothing is uploaded.
```

- [ ] **Step 2: Add the architecture paragraph to `CLAUDE.md`**

Insert after the "Failure isolation" paragraph in the Architecture section:

```markdown
**The UI (`ui/`) and launcher (`launcher/`, `Start.bat`) are a layer above the
pipeline, never inside it.** `ui/jobs.py` imports `process_file` from `main.py`
and runs it on a worker thread; it never re-implements extraction. Nothing
under `extractor/` and nothing in `main.py` may be modified to serve the UI —
that rule is what makes the baseline snapshot and golden corpus passing a
proof that the pipeline is unchanged, and it is what lets the whole add-on be
deleted without trace. `ui/quality.py` owns the statistics and the
pass/review/fail gate table; add a new warning code there, in one place, not
in the page. All user-facing wording comes from `extractor/warning_text.py`,
whose third consumer the UI now is.
```

- [ ] **Step 3: Add a `CHANGELOG.md` entry**

Follow the file's existing heading style. Content:

```markdown
### Added
- `Start.bat` and `launcher/setup.ps1`: a double-click launcher that finds or
  installs Python, builds the virtual environment, and opens the UI. Every
  step checks before it acts, so a second run costs about a second.
- `ui/`: a local single-page web UI on loopback. Native Windows folder and
  file pickers, byte-weighted progress with per-file durations and an ETA,
  and output written to `Extracted/` beside the source files.
- Per-file quality verdicts (pass / review / fail), statistics including
  characters extracted and an approximate token count, and a text preview.
  A document that extracts to zero characters is reported as failed rather
  than converted.
- `Extracted/report.md` and `Extracted/report.json` summarizing each run.

No change to the extraction pipeline: `extractor/` and `main.py` are untouched.
```

- [ ] **Step 4: Full verification**

Run each and confirm the output before claiming completion:

```bash
.venv/Scripts/python.exe -m pytest              # full suite, slow tests included
.venv/Scripts/ruff check .
git status --short                              # no unintended files
git diff --stat v1.1-pre-ocr..HEAD -- extractor main.py
```

The last command is the important one: it must show **no changes** to
`extractor/` or `main.py` from this plan's work. If it shows any, that is a
violation of the global constraint — stop and report it.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md CHANGELOG.md
git commit -m "Document the launcher and the UI layer

States the rule that ui/ and launcher/ import from main.py rather than
reimplement it, so a future change knows where the boundary is."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| 3 — frozen pipeline | Global constraints; Task 9 Step 4 verifies it |
| 4.1 — stdlib only | Global constraints; Task 6 |
| 4.2 — picker / jobs / quality / report / server | Tasks 5, 3, 1, 4, 6 |
| 4.3 — output beside source | Task 2 (`output_dirs`), Task 3 |
| 4.4 — loopback, token, path safety, idle exit | Task 6 |
| 5 — bootstrap, all 7 steps | Task 8 |
| 6.1 — weighted bar, elapsed timer, ETA | Task 3 (`snapshot`, `_eta`), Task 7 |
| 6.2 — durations | Task 3 |
| 6.3 — statistics table | Task 1 (`collect_stats`) |
| 6.4 — gates, headline, sorting, preview | Task 1, Task 4, Task 7 |
| 6.5 — stop after current file | Task 3 (`request_stop`) |
| 7 — report files | Task 4 |
| 8 — the screen, three states, layered detail | Task 7 |
| 9 — no advanced options, `config` in the body | Task 6 (`_convert` reads `max_file_mb`, `min_confidence`) |
| 11 — testing | Tasks 1–6 |
| 12 — documentation | Task 9 |
| 14 — definition of done | Task 9 Step 4 |

**Known gaps, deliberate:**
- The page (Task 7) and the bootstrap (Task 8) have no automated tests, per spec section 11.
- The spec's "page limit truncated the document" review trigger is dropped. `MAX_PAGES` is defined in `extractor/limits.py` and `--max-pages` is parsed in `main.py`, but the value reaches no reader, so no truncation happens and no warning is emitted. A gate for it could never fire. Adding the enforcement is a pipeline change and out of scope here.

**Type consistency:** `verdict` is one of `quality.PASS/REVIEW/FAIL` in Tasks 1, 3, 4, and the JS `LABEL`/`ORDER` maps in Task 7. The status dict keys defined in Task 3 are the ones read in Task 6 (`_status` passes it through) and Task 7 (`render`, `finish`). `write_report(report_dir, results, summary)` has the same signature in the Task 3 stub, the Task 4 implementation, and the Task 3 test monkeypatch.
