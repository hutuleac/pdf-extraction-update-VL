# Main Repository Extraction Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the existing local extraction pipeline so it produces cleaner text and tables, validates strong file signatures, fails visibly and consistently, and remains protected by targeted regression tests.

**Architecture:** Preserve the current extension-based dispatcher, shared model constructors, PDF/OCR/VLM pipeline, warning codes, per-file isolation, JSON/Markdown writers, and `schema_version = "2.0"`. Add a small fatal-error taxonomy and bounded content-validation layer around the existing readers; add reader-local quality improvements only where tests prove better text or table output. Task 4 owns the decision on minimal heading/list metadata; no metadata is added unless the quality tests demonstrate a need. Keep Phase 3 RAG relationships, asset provenance, rich inline semantics, AnyDoc, and ZIP expansion budgets out of this plan.

**Tech Stack:** Python 3.11+, pytest, pytest-cov, Ruff, PyMuPDF, pdfplumber, python-docx, openpyxl, python-pptx, BeautifulSoup, defusedxml, Pillow.

**Spec:** `docs/backlog-main-repo-proposal.md` (Scope, Priority 0, Priority 1, Priority 2, and Recommended implementation order; exclude the Phase 3 section).

## Global Constraints

- Preserve the existing JSON and Markdown output contract; optional additive fields must not be required to read `content`.
- Keep `schema_version = "2.0"` unless a separately approved schema decision proves that compatibility cannot be preserved.
- Do not add AnyDoc, legacy-format expansion, hosted OCR, runtime downloads, Rust, ZIP decompression limits, or total archive-expansion limits.
- Do not silently truncate text, tables, rows, cells, or pages because of a new safety limit; refuse the file with a structured `resource_limit` failure.
- Keep recoverable quality issues as structured warning codes; fatal failures must not be represented as warnings on a nonexistent document.
- Keep current PDF, OCR, and VLM behavior authoritative unless a regression test proves that a change improves recovery without duplication or reading-order loss.
- Preserve baseline snapshots and golden semantic thresholds; intentional output changes require explicit fixture and snapshot review.
- Match existing Python style: focused helpers, type hints, no speculative abstraction, and per-reader error isolation only where the reader already uses it.
- Run targeted tests after each task, then the full suite and Ruff before claiming completion.

## File Map

### New files

- `extractor/errors.py` — stable fatal-error categories and typed extraction failure used by the dispatcher, readers, and batch runner.
- `extractor/format_detection.py` — bounded strong-signature and OOXML package validation; no ambiguous-format guessing.
- `tests/test_errors.py` — taxonomy mapping and serialization/diagnostic tests.
- `tests/test_format_detection.py` — valid, mismatched, malformed, and ambiguous signature tests.
- `tests/test_robustness.py` — malformed-package, resource-limit, mutation, and no-hang regression tests that do not belong to one reader.

### Existing files to modify

- `extractor/dispatcher.py` — validate strong signatures before invoking the extension-selected reader; retain extension fallback for ambiguous formats; accept an explicit override if the CLI/API exposes it in the same change.
- `main.py` — map typed fatal failures to stable batch results and diagnostics without changing exit-code semantics.
- `extractor/limits.py` — add narrowly scoped JSON, XML, image, and table safety budgets while retaining existing file and workbook limits.
- `extractor/json_reader.py` — enforce depth/container limits without partial silent output; emit or raise the agreed typed failure.
- `extractor/xml_reader.py` — enforce depth/node limits around the existing `defusedxml` parser and renderer.
- `extractor/image_reader.py` and/or `extractor/ocr/raster.py` — enforce the configured pixel budget before expensive rasterization and classify refusal as `resource_limit`.
- `extractor/model.py` — add only optional heading/list fields if Task 4 proves they improve current extraction and the field shape is approved by tests.
- `extractor/docx_reader.py` — improve heading/list/display-text/formula or embedded-text extraction in separate, measured slices.
- `extractor/table_reader.py`, `extractor/html_reader.py`, `extractor/xlsx_reader.py`, `extractor/pptx_reader.py`, and relevant table-producing readers — improve normalization, ordering, duplicate suppression, and table-size refusal only where the named Task 5 fixtures demonstrate a defect.
- `tests/test_dispatcher.py`, `tests/test_cli.py`, `tests/test_limits.py`, `tests/test_json_reader.py`, `tests/test_new_readers.py`, `tests/test_docx_reader.py`, `tests/test_table_reader.py`, `tests/test_xlsx_reader.py`, and relevant end-to-end/golden tests — extend existing coverage instead of duplicating fixtures.
- `README.md` — document stable fatal categories, new user-visible limits, and any explicit format override only after behavior is implemented and verified.

---

### Task 1: Define stable fatal-error taxonomy

**Files:**
- Create: `extractor/errors.py`
- Create: `tests/test_errors.py`
- Modify: `main.py:174-218, 301-366`
- Modify: `extractor/dispatcher.py:40-56`
- Test: `tests/test_cli.py`, `tests/test_dispatcher.py`

**Interfaces:**
- Produces `FatalErrorCategory(str, Enum)` with exactly `UNSUPPORTED`, `MALFORMED`, `ENCRYPTED`, `RESOURCE_LIMIT`, `MISSING_PART`, and `IO` values serialized as lowercase strings.
- Produces `ExtractionError(Exception)` with `category: FatalErrorCategory`, `path: Path | None`, and a human-readable message; its `__str__` must not include a traceback or duplicate the category.
- Produces `classify_exception(exc: Exception) -> ExtractionError`, with explicit mappings for existing `FileTooLargeError` and `WorkbookTooLargeError`, unsupported-extension `ValueError`, common malformed/encrypted package exceptions, `FileNotFoundError`/`PermissionError`, and an `IO` fallback for filesystem errors. The function re-raises unknown programming exceptions; it never returns a misleading category for them.
- `main.process_file` and the batch loop continue returning the existing model/summary shapes, but failed results gain `error_category` alongside the existing human-readable `error`.

- [ ] **Step 1: Write failing taxonomy tests**

```python
from pathlib import Path

import pytest

from extractor.errors import (
    ExtractionError,
    FatalErrorCategory,
    classify_exception,
)
from extractor.limits import FileTooLargeError


def test_resource_limit_error_has_stable_category(tmp_path):
    exc = FileTooLargeError(tmp_path / "large.pdf", 20, 10)
    result = classify_exception(exc)
    assert isinstance(result, ExtractionError)
    assert result.category is FatalErrorCategory.RESOURCE_LIMIT
    assert str(result) == str(exc)


def test_unknown_exception_is_not_silently_reclassified():
    with pytest.raises(RuntimeError, match="bug"):
        classify_exception(RuntimeError("bug"))
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run: `python -m pytest tests/test_errors.py -v`

Expected: collection or assertion failure because `extractor.errors` and its taxonomy do not yet exist.

- [ ] **Step 3: Implement the minimal taxonomy and classification helpers**

Implement `classify_exception` so known operational/library failures return `ExtractionError`, while unknown exceptions are re-raised unchanged. Use a string enum so JSON/log consumers receive stable lowercase values:

```python
class FatalErrorCategory(str, Enum):
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    ENCRYPTED = "encrypted"
    RESOURCE_LIMIT = "resource_limit"
    MISSING_PART = "missing_part"
    IO = "io"
```

Keep exception classification explicit. Catch only known library exception types or stable message signatures for malformed/encrypted packages; re-raise unknown exceptions so coding defects remain visible during tests.

- [ ] **Step 4: Integrate taxonomy into batch results without changing exit codes**

In `main.py`, convert only expected extraction failures to `ExtractionError`, append `{"error_category": exc.category.value, "error": str(exc), "ok": False}`, and keep exit code `1` for any failed file. Keep the current `2` for a missing input directory. Add the category to the failed summary line.

- [ ] **Step 5: Run focused regression tests**

Run: `python -m pytest tests/test_errors.py tests/test_dispatcher.py tests/test_cli.py -v`

Expected: all focused tests pass, including existing unsupported-extension and exit-code tests.

- [ ] **Step 6: Commit the independent deliverable**

```bash
git add extractor/errors.py main.py extractor/dispatcher.py tests/test_errors.py tests/test_dispatcher.py tests/test_cli.py
git commit -m "feat: add stable fatal extraction errors"
```

---

### Task 2: Add strong-signature content validation

**Files:**
- Create: `extractor/format_detection.py`
- Create: `tests/test_format_detection.py`
- Modify: `extractor/dispatcher.py:40-56`
- Modify: `tests/test_dispatcher.py`
- Modify: `README.md` after behavior is complete

**Interfaces:**
- Produces `validate_path(path: Path, declared_suffix: str) -> None`, raising `ExtractionError` only for strong mismatches, malformed packages, encrypted packages, or missing required package parts.
- Produces `detect_package_kind(path: Path) -> str | None` for strong signatures only: `pdf`, `rtf`, `ole`, `docx`, `xlsx`, `pptx`, or `None` for ambiguous/plain formats.
- `extract_document(path, *, images_dir=None, format_override=None)` validates the selected reader unless `format_override` explicitly requests a known reader. The default remains extension dispatch.

- [ ] **Step 1: Build test fixtures for valid and mismatched strong signatures**

Add tests using existing runtime fixtures where possible:

```python
def test_pdf_extension_with_non_pdf_bytes_is_malformed(tmp_path):
    path = tmp_path / "wrong.pdf"
    path.write_bytes(b"plain text, not a PDF")
    with pytest.raises(ExtractionError) as raised:
        extract_document(path)
    assert raised.value.category.value == "malformed"


def test_txt_keeps_extension_fallback(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("%PDF-1.7 is ordinary text here", encoding="utf-8")
    model = extract_document(path)
    assert model["document"]["source_type"] == "txt"
```

Also test DOCX/XLSX/PPTX package content types and a ZIP package with missing content types.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_format_detection.py tests/test_dispatcher.py -v`

Expected: failure because the validator and dispatcher integration do not yet exist.

- [ ] **Step 3: Implement bounded signature detection**

Read only the minimum bytes or package members needed for validation. Use standard-library `zipfile` for package inspection and existing safe XML handling for content-type/root checks. Do not scan arbitrary package contents or infer CSV/TXT/Markdown/HTML/JSON/XML types.

Validation rules:

- `.pdf` requires `%PDF-` near the start of the file.
- `.rtf` requires an `{"\\rtf"}` header.
- OOXML extensions require a readable ZIP package, `[Content_Types].xml`, and the expected main-document content type.
- OLE detection identifies the compound-file signature but does not add legacy-format support; an OLE file using a currently unsupported extension remains `unsupported`.
- A strong signature mismatch raises `malformed`; an unsupported declared extension remains `unsupported`.

- [ ] **Step 4: Integrate validation without changing ambiguous routing**

Call `validate_path` after extension lookup and before the reader. Preserve `.csv`, `.txt`, `.md`, `.html`, `.json`, and `.xml` extension fallback. Ensure `.xlsm` uses the existing XLSX reader and correct macro-enabled content type validation.

- [ ] **Step 5: Add explicit override only if tests establish a real use case**

If the existing CLI/API has no user-facing override, do not add one in this task. The validator must not create an unused option. If an override is added, test that it selects only an existing reader and never bypasses resource limits.

- [ ] **Step 6: Run dispatcher and end-to-end tests**

Run: `python -m pytest tests/test_format_detection.py tests/test_dispatcher.py tests/test_end_to_end.py -v`

Expected: all pass, with existing supported-format routing unchanged.

- [ ] **Step 7: Document the validation behavior and commit**

Update the supported-format/diagnostics section of `README.md` with strong-signature validation and the ambiguous-format fallback rule.

```bash
git add extractor/format_detection.py extractor/dispatcher.py tests/test_format_detection.py tests/test_dispatcher.py tests/test_end_to_end.py README.md
git commit -m "feat: validate strong file signatures before dispatch"
```

---

### Task 3: Add targeted JSON, XML, image, and table safety budgets

**Files:**
- Modify: `extractor/limits.py`
- Modify: `extractor/json_reader.py`
- Modify: `extractor/xml_reader.py`
- Modify: `extractor/image_reader.py` and/or `extractor/ocr/raster.py`
- Modify: the specific table-producing reader identified by the Task 3 fixture and allocation-point check; name that reader in the implementation diff before adding the guard
- Modify: `tests/test_limits.py`
- Create or modify: `tests/test_robustness.py`, `tests/test_json_reader.py`, `tests/test_new_readers.py`, `tests/test_table_reader.py`

**Interfaces:**
- Extends Task 1's `ExtractionError` with `ResourceLimitError(ExtractionError)`, carrying `limit_name`, `observed`, and `allowed` fields; its category is always `FatalErrorCategory.RESOURCE_LIMIT` and its string form identifies the limit and values.
- Produces focused checks such as `check_json_limits`, `check_xml_limits`, `check_image_pixels`, and `check_table_dimensions`; each raises before the expensive expansion it protects.
- Existing workbook row/cell refusal remains unchanged and maps to `resource_limit`.
- New limits fail the file; they never return partial content with a warning.

- [ ] **Step 1: Choose documented defaults from measured current workloads**

Before coding, inspect the golden corpus and current fixtures to record maximum normal depth, node count, image pixels, and table dimensions. Add constants with names and units, for example:

```python
MAX_JSON_DEPTH = 100
MAX_JSON_CONTAINERS = 1_000_000
MAX_XML_DEPTH = 100
MAX_XML_NODES = 1_000_000
MAX_IMAGE_PIXELS = 100_000_000
MAX_TABLE_CELLS = 5_000_000
```

Do not copy these values blindly if corpus measurements show a normal document exceeds them; the committed constants must be justified in a comment or README table.

- [ ] **Step 2: Write failing limit tests**

Test each breach and one normal boundary:

```python
def test_json_depth_breach_is_resource_limit(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("{" + "\"x\":{" * (MAX_JSON_DEPTH + 1) + "null" + "}" * (MAX_JSON_DEPTH + 1), encoding="utf-8")
    with pytest.raises(ExtractionError) as raised:
        extract_json(path)
    assert raised.value.category.value == "resource_limit"
```

Add equivalent tests for XML node/depth, image pixel count, and table dimensions. Verify that no JSON/Markdown output is written for a refused file.

- [ ] **Step 3: Run focused tests and verify failure**

Run: `python -m pytest tests/test_limits.py tests/test_json_reader.py tests/test_new_readers.py tests/test_table_reader.py tests/test_robustness.py -v`

Expected: new tests fail because the new checks and exception type do not exist.

- [ ] **Step 4: Implement checks at the earliest safe enforcement point**

For JSON, replace the current warning-and-truncation path with a bounded parse/traversal that raises `ResourceLimitError` before recursive rendering when depth or container count exceeds the configured limit. Do not retain an unbounded duplicate representation merely to count it.

For XML, count nodes and maximum depth during a bounded traversal after safe parsing and before rendering; raise `ResourceLimitError` instead of emitting `NESTING_TRUNCATED`. Keep `defusedxml` protections. If the parser itself rejects the input, classify it as malformed rather than resource limit unless the configured budget was exceeded.

For images, inspect dimensions before allocating the full RGB array; reject pixel counts above the configured cap. Keep OCR-unavailable behavior as a warning when the image is valid and within budget.

For tables, check dimensions before materializing or normalizing all rows where the reader exposes dimensions. Keep merged-cell expansion as an internal guard only; do not add span metadata. If a reader cannot know dimensions before materialization, count incrementally and raise before appending a row that exceeds the limit; never return partial table content.

- [ ] **Step 5: Map limit failures into batch diagnostics**

Ensure `main.py` reports `resource_limit` and the limit name while preserving exit code `1` and per-file isolation.

- [ ] **Step 6: Run the focused and full non-slow suites**

Run: `python -m pytest tests/test_limits.py tests/test_json_reader.py tests/test_new_readers.py tests/test_table_reader.py tests/test_robustness.py -v`

Then run: `python -m pytest -m "not slow"`

Expected: both commands pass with no partial output from refused inputs.

- [ ] **Step 7: Document defaults and commit**

Add a concise limits table to `README.md`, then commit:

```bash
git add extractor/limits.py extractor/json_reader.py extractor/xml_reader.py extractor/image_reader.py extractor/ocr/raster.py tests/test_limits.py tests/test_json_reader.py tests/test_new_readers.py tests/test_table_reader.py tests/test_robustness.py README.md
git commit -m "feat: enforce bounded extraction resource limits"
```

---

### Task 4: Improve DOCX headings and lists without adding relationship metadata

**Files:**
- Modify: `extractor/docx_reader.py`
- Modify: `extractor/model.py` only if optional heading/list fields are approved by the tests
- Modify: `extractor/markdown_writer.py` only if existing blocks need compatible rendering
- Modify: `tests/test_docx_reader.py`
- Modify: `tests/test_model.py` and `tests/test_markdown_writer.py` if model fields change

**Interfaces:**
- Existing `extract_docx(path) -> dict` remains the public reader interface.
- Text content remains in existing `type: "text"` blocks.
- If metadata is added, use optional keys such as `heading_level` and `list_level`; absence must preserve current JSON shape and Markdown behavior.
- Do not add hyperlink targets, anchors, footnote graphs, asset IDs, or a new inline-run model.

- [ ] **Step 1: Add runtime DOCX fixtures for the first quality slice**

Create a document containing:

- Heading 1 and Heading 2 paragraphs.
- A numbered paragraph followed by a nested list paragraph.
- A paragraph containing hyperlink display text.
- A table between paragraphs.

Assert the extracted block order and exact cleaned text. Keep the fixture generation in `tests/conftest.py` or the existing test fixture pattern.

- [ ] **Step 2: Write failing behavior tests**

```python
def test_docx_preserves_heading_and_list_text(docx_with_structure):
    model = extract_docx(docx_with_structure)
    blocks = model["pages"][0]["content"]
    assert [block["type"] for block in blocks] == ["text", "text", "table", "text"]
    assert "Project heading" in blocks[0]["content"]
    assert "First list item" in blocks[1]["content"]
```

Add assertions for display text and that tables remain separate from surrounding paragraphs.

- [ ] **Step 3: Run tests to verify the quality gap**

Run: `python -m pytest tests/test_docx_reader.py -v`

Expected: the new fixture assertions fail or expose the exact missing structure; do not proceed if the current reader already satisfies them—replace the test with the next measured gap instead of adding needless code.

- [ ] **Step 4: Implement the smallest reader-local fix**

Walk body children in existing order. Read paragraph style names and numbering properties only to preserve heading/list text and optional classification. Keep consecutive ordinary paragraphs grouped as today. Preserve hyperlink display text by traversing the paragraph's visible `w:t` descendants, including text inside `w:hyperlink`; do not add target relationships. Preserve formulas or embedded text only when the fixture demonstrates that the source text exists and can be recovered without duplicating visible content.

- [ ] **Step 5: Add optional metadata only if it improves current output**

If heading/list classification is needed by current Markdown or clean-text behavior, add optional fields through model constructors with omission-by-default. Add a model test proving old callers receive the old shape for ordinary text blocks.

- [ ] **Step 6: Run DOCX and compatibility tests**

Run: `python -m pytest tests/test_docx_reader.py tests/test_model.py tests/test_markdown_writer.py tests/test_end_to_end.py -v`

Expected: new structure tests pass and existing shape/output tests remain green.

- [ ] **Step 7: Commit the quality slice**

```bash
git add extractor/docx_reader.py extractor/model.py extractor/markdown_writer.py tests/test_docx_reader.py tests/test_model.py tests/test_markdown_writer.py tests/test_end_to_end.py
git commit -m "feat: improve docx structural text extraction"
```

---

### Task 5: Improve table normalization and duplicate suppression across existing readers

**Files:**
- Modify: `extractor/model.py`
- Modify: `extractor/table_reader.py`
- Modify: `extractor/docx_reader.py`, `extractor/xlsx_reader.py`, `extractor/pptx_reader.py`, and `extractor/html_reader.py` only where tests identify inconsistent behavior
- Modify: `extractor/pdf_reader.py` only where table/text exclusion tests identify duplication
- Modify: `tests/test_table_reader.py`, `tests/test_docx_reader.py`, `tests/test_xlsx_reader.py`, `tests/test_new_readers.py`, and relevant PDF/HTML tests

**Interfaces:**
- `make_table_block(rows: list[list[str]]) -> dict` remains the single constructor for public table blocks.
- Table content remains `block["content"]` as normalized rows; no public merged-cell metadata is added.
- Reader-specific filtering may reject a false-positive table, but rejected prose must remain available to the normal text path where the current architecture supports it.

- [ ] **Step 1: Build a table-quality matrix from existing fixtures**

Record expected behavior for each reader:

- Empty cell becomes `""`.
- Row and column order is preserved.
- Cell whitespace is normalized consistently.
- A prose box is not emitted as a table when the reader can return it as text.
- Table text is not duplicated in surrounding text blocks.
- Repeated chart rows are rejected only when the existing heuristic identifies them.

- [ ] **Step 2: Add failing cross-format tests for one defect at a time**

Use separate named fixtures for each reader, and parameterize only the shared assertion over already-created models:

```python
@pytest.mark.parametrize("model", [docx_model, xlsx_model, html_model])
def test_table_cells_are_normalized_consistently(model):
    tables = [
        block
        for unit in model["pages"]
        for block in unit["content"]
        if block["type"] == "table"
    ]
    assert tables
    assert all(
        cell == cell.strip()
        for row in tables[0]["content"]
        for cell in row
    )
```

Implement `docx_model`, `xlsx_model`, and `html_model` as concrete test fixtures using the repository's existing runtime fixture helpers; do not introduce an undefined `fixture_for_reader` abstraction. Keep PDF-specific geometry and false-positive tests in `tests/test_table_reader.py` and PDF tests.

- [ ] **Step 3: Run tests to verify the defect**

Run: `python -m pytest tests/test_table_reader.py tests/test_docx_reader.py tests/test_xlsx_reader.py tests/test_new_readers.py -v`

Expected: each newly added test fails only for a demonstrated inconsistency.

- [ ] **Step 4: Implement shared normalization and reader-local filtering**

Use `make_table_block` for all table output. Do not add another normalization utility unless the existing `normalize` function cannot express the required behavior. Preserve empty cells as empty strings, retain row lengths where the source provides them, and avoid converting a table to prose merely to simplify rendering.

For PDF, preserve the coordinate exclusion path so rejected false-positive tables return to native text. For DOCX/XLSX/PPTX/HTML, keep source order and only remove duplicate content when a test proves the same text is emitted twice.

- [ ] **Step 5: Run table and end-to-end regressions**

Run: `python -m pytest tests/test_table_reader.py tests/test_docx_reader.py tests/test_xlsx_reader.py tests/test_new_readers.py tests/test_pdf_reader.py tests/test_end_to_end.py -v`

Expected: all table tests pass and no baseline/golden output changes occur unintentionally.

- [ ] **Step 6: Commit the table-quality deliverable**

```bash
git add extractor/model.py extractor/table_reader.py extractor/docx_reader.py extractor/xlsx_reader.py extractor/pptx_reader.py extractor/html_reader.py extractor/pdf_reader.py tests/test_table_reader.py tests/test_docx_reader.py tests/test_xlsx_reader.py tests/test_new_readers.py tests/test_pdf_reader.py tests/test_end_to_end.py
git commit -m "fix: normalize and de-duplicate extracted tables"
```

---

### Task 6: Verify native-text authority and recovery non-duplication

**Files:**
- Modify: `extractor/pdf_reader.py`, `extractor/ocr/apply.py`, `extractor/vlm/apply.py` only if a regression test exposes a violation
- Modify: `tests/test_ocr_apply.py`, `tests/test_vlm_apply.py`, `tests/test_pdf_reader.py`, `tests/test_ocr_pipeline.py`, `tests/test_vlm_pipeline.py`
- Modify: `tests/test_end_to_end.py` for a representative native/recovery fixture

**Interfaces:**
- Native text remains the default source for healthy pages.
- OCR/VLM blocks remain source-tagged using existing `source` values and warning behavior.
- Recovery output must be additive only when it contains novel content, or replacing only when the existing page-class rules authorize replacement.

- [ ] **Step 1: Add tests for healthy native text and recovery overlap**

Test that:

- A native page with an OCR/VLM reading identical to native text emits no duplicate recovery block.
- A scanned or garbled page can use authorized replacement behavior.
- A low-confidence recovery result remains rejected or warned according to the existing policy.
- An OCR/VLM failure does not discard already extracted native text.

- [ ] **Step 2: Run the focused recovery tests**

Run: `python -m pytest tests/test_ocr_apply.py tests/test_vlm_apply.py tests/test_pdf_reader.py tests/test_ocr_pipeline.py tests/test_vlm_pipeline.py -v`

Expected: existing behavior passes; any new failure identifies a concrete duplication or authority defect.

- [ ] **Step 3: Change only the failing recovery decision**

Do not rewrite the OCR/VLM pipeline. Use the existing page classification, novelty, confidence, warning, and skip mechanisms. Add a regression test before changing a decision predicate.

- [ ] **Step 4: Run all recovery tests and commit**

Run: `python -m pytest tests/test_ocr_apply.py tests/test_vlm_apply.py tests/test_pdf_reader.py tests/test_ocr_pipeline.py tests/test_vlm_pipeline.py tests/test_end_to_end.py -v`

```bash
git add extractor/pdf_reader.py extractor/ocr/apply.py extractor/vlm/apply.py tests/test_ocr_apply.py tests/test_vlm_apply.py tests/test_pdf_reader.py tests/test_ocr_pipeline.py tests/test_vlm_pipeline.py tests/test_end_to_end.py
git commit -m "test: protect native text and recovery boundaries"
```

---

### Task 7: Add focused robustness and mutation coverage

**Files:**
- Create: `tests/test_robustness.py`
- Modify: `tests/conftest.py` only for reusable bounded fixtures
- Modify: `tests/test_dispatcher.py` and `tests/test_cli.py` for stable category and isolation assertions
- Modify: reader-specific test files only when a robustness case belongs to that reader; list the exact file in the task before editing

**Interfaces:**
- Tests must exercise public extraction and CLI interfaces, not private implementation details unless testing a pure safety helper.
- Every malformed or refused file must leave the batch able to process the next valid file.
- No test may require network access, model downloads, or platform-specific Rust tooling.

- [ ] **Step 1: Add malformed and encrypted-package tests**

Create deterministic byte fixtures for:

- Invalid PDF bytes with `.pdf` extension.
- Invalid ZIP/OOXML package with `.docx`, `.xlsx`, and `.pptx` extensions.
- Valid package with missing required main part.
- Encrypted Office package if the library exposes a deterministic fixture; otherwise assert the typed mapping using a mocked reader exception without inventing a binary fixture.

- [ ] **Step 2: Add per-file isolation and no-output-on-failure tests**

Run the CLI or `main.main([...])` against a directory containing one malformed file and one valid file. Assert exit code `1`, valid output exists, failed output does not, and the summary contains the stable category.

- [ ] **Step 3: Add deterministic mutation tests**

Use a small valid PDF fixture and a small valid DOCX fixture generated by existing test helpers. For each fixture, copy the bytes, flip one byte at each of three fixed offsets that are within the file, and write the mutated copy with the original extension. Assert that extraction either succeeds with a model containing `schema_version` and at least one unit or raises `ExtractionError` with a stable category; it must not hang, emit partial files, or crash the batch process. Do not mutate ZIP central-directory bytes or rely on random offsets, because those tests would be nondeterministic across library versions.

- [ ] **Step 4: Run robustness tests**

Run: `python -m pytest tests/test_robustness.py tests/test_dispatcher.py tests/test_cli.py -v`

Expected: all robustness tests pass and complete within the repository's normal test timeout. If a mutation exposes an unrelated library crash, convert that case into a typed failure at the reader boundary or narrow the mutation to the documented malformed-input contract; do not weaken the assertion to accept a traceback or partial output.

- [ ] **Step 5: Commit test coverage**

```bash
git add tests/test_robustness.py tests/conftest.py tests/test_dispatcher.py tests/test_cli.py
 git commit -m "test: cover malformed and adversarial inputs"
```

---

### Task 8: Final compatibility review and quality gates

**Files:**
- Modify: `README.md` if any documented defaults or fatal categories changed during implementation.
- Modify: `CHANGELOG.md` with the approved user-visible changes.
- Review only: all files changed by Tasks 1–7.

**Interfaces:**
- No new public interface is introduced in this task; this is a verification and documentation gate.
- Baseline/golden outputs remain the compatibility authority.

- [ ] **Step 1: Run the complete non-slow suite**

Run: `python -m pytest -m "not slow"`

Expected: exit code `0`, zero failures, zero errors, and no unexpected skips beyond registered slow/integration behavior.

- [ ] **Step 2: Run baseline and golden tests explicitly**

Run: `python -m pytest tests/test_baseline_snapshot.py tests/golden -v`

Expected: all baseline snapshots and golden semantic metrics pass.

- [ ] **Step 3: Run Ruff**

Run: `python -m ruff check .`

Expected: exit code `0` and no diagnostics.

- [ ] **Step 4: Review output compatibility**

Compare generated output for the representative corpus before and after the changes. Approve only intentional differences: clearer failure categories, improved clean text/tables, and explicitly approved optional metadata. Reject changes that add duplicate native/recovery text, reorder existing content, or silently drop content.

- [ ] **Step 5: Update documentation and commit the release-facing changes**

Document the final limits, fatal categories, validation rules, and intentional output changes. Then run the three verification commands again after documentation-only edits if source files were touched by the documentation update.

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document extraction safety and quality improvements"
```

## Self-Review Checklist

- [x] The plan covers the pre-Phase-3 scope: fatal taxonomy, strong-signature validation, targeted limits, DOCX quality, table quality, recovery authority, robustness tests, and minimal optional structure.
- [x] AnyDoc, ZIP expansion budgets, asset provenance/deduplication, relationship graphs, and RAG-specific work are excluded.
- [x] Existing workbook limits are preserved rather than duplicated.
- [x] Unknown programming errors are not silently converted into user-facing file failures.
- [x] Every new public behavior has a named interface and focused tests.
- [x] Every task has a failing-test, implementation, verification, and commit sequence.
- [x] No task requires a schema-version change by default.
- [x] The final gates include the non-slow suite, baseline/golden tests, and Ruff.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-15-main-repo-extraction-improvements.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task with review checkpoints.
2. **Inline Execution** — execute tasks in this session with checkpoint reviews.
