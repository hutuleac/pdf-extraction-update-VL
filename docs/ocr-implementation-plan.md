# OCR for the Knowledge Extraction Pipeline

> **Status: implemented.** 185 tests green, ruff clean. This document is the
> design as agreed, followed by what actually changed during implementation —
> see "What changed during implementation" at the end. Read that section too;
> five decisions here were overtaken by evidence.

## Context

The pipeline converts 11 document formats to JSON + Markdown, fully offline. It already
*detects* pages it cannot read — `page_signals.py` classifies every PDF page as
`native-text` / `scanned` / `mixed` / `garbled` / `layout-complex` and emits a
`SCANNED_PAGE_NO_TEXT` warning — but it has no way to *read* them. Scanned pages produce
empty output today. OCR closes that gap.

A local OCR model exists at `C:\Models\ocr` (PaddleOCR PP-OCRv6_small, ONNX). The
integration must not make that model mandatory: on any machine without it, the repo must
behave exactly as it does today and state plainly which content it could not extract,
rather than emitting silently-empty pages.

### Verified facts (checked during planning, not assumed)

- **Models load and are sound.** `ch_PP-OCRv6_small_det_infer.onnx` (9.4 MB) outputs a DB
  probability map; `ch_PP-OCRv6_small_rec_infer.onnx` (20.2 MB) takes fixed height 48 and
  outputs 18,710 CTC classes. Confirmed by loading both in onnxruntime.
- **The missing dictionary is solved.** No char dict ships with the model. PaddleOCR's
  `ppocrv6_dict.txt` has 18,708 entries; `blank + 18,708 + space = 18,710` — an exact match.
- **The whole pipeline is proven, not assumed.** A spike ran the real models end to end on a
  rendered 4-line page: detection found exactly 4 regions for 4 lines, and det → crop →
  perspective-warp → rec → CTC decode returned **3/4 lines character-perfect at 0.98–1.00
  confidence in 0.35 s/page on CPU**. The 4th line differed only by one collapsed gap
  (`"Owner: HR Team  Date:"` → `"HR TeamDate"`): a two-column split that my crude padding
  stand-in merged, and exactly what the real `pyclipper` unclip step exists to fix.
  This retires the main technical risk — the fallback engine is known-viable, not hoped-for.
- **The HF cache in the model folder is broken.** `models--*/snapshots/*/inference.onnx`
  are 1 KB Cygwin `XSym` symlink stubs (copied from macOS). Only the two flat `.onnx`
  files are usable. Any `huggingface_hub` resolution against that folder returns garbage.
- **`models.json` / `setup.md` / `inference.py` in the model folder are wrong** (claim
  height 32, 50/100 MB sizes, v4 download URLs, no post-processing). Ignore them.
- **Environment:** onnxruntime 1.28 CPU-only (no CUDA/DirectML EP), numpy, opencv, Pillow,
  PyMuPDF present. `pyclipper`, `rapidocr`, `paddleocr`, Tesseract absent. PyPI reachable.
- **Baseline is green:** 140 tests pass on `d26469d`.
- **Cost is acceptable:** 0.07 s/page to rasterize at 300 DPI plus ~0.35 s/page to OCR on
  CPU. Rasterizing produces 27 MB frames on landscape slides, so a pixel budget is needed;
  only scanned/garbled/mixed pages pay this cost at all.
- **No real scanned pages exist in `input/`**, so acceptance needs a synthetic fixture.

### Decisions taken

Dual engine with rapidocr preferred and a hand-rolled ONNX fallback; OCR scanned + garbled
+ mixed PDF pages **and** standalone image files; auto-on when available with `--no-ocr`;
baseline as git tag plus a committed output snapshot.

---

## Task 0 — Baseline

Tag the current known-good state:

```bash
git tag -a v1.1-pre-ocr -m "Baseline before OCR: 11 formats, 140 tests green"
```

**Snapshot caveat — resolved, needs your awareness.** `.gitignore` excludes `input/*` as
"may be sensitive" and `output/json/*.json`. Your corpus includes a customer RFQ and
meeting documents, so committing their extracted text would push customer content to the
remote. The snapshot is therefore taken from the **deterministic golden fixtures**, not
from `input/`:

- New `tests/baseline/` holds JSON rendered from `tests/golden/conftest.py` fixtures,
  produced by a small `tests/baseline/generate.py`, committed.
- A new test asserts current output still matches that snapshot — this is what proves OCR
  changed nothing for non-scanned documents.
- A real-input snapshot can be generated locally at any time; it stays gitignored.

Revert path: `git checkout v1.1-pre-ocr`.

---

## Task 1 — OCR core, engine-agnostic

New package `extractor/ocr/`. Nothing here imports onnxruntime/rapidocr at module level.

| File | Responsibility |
|------|----------------|
| `__init__.py` | Public surface: `get_engine()`, `OcrResult`, `OcrLine`, `OcrUnavailable` |
| `base.py` | `OcrEngine` protocol, `OcrResult`/`OcrLine` dataclasses, `OcrUnavailable` reason enum |
| `registry.py` | Engine discovery, availability probing, selection order, caching |
| `paths.py` | Model/dict path resolution |
| `rapidocr_engine.py` | rapidocr adapter (preferred) |
| `onnx_engine.py` | Hand-rolled fallback: det + rec orchestration |
| `dbnet_post.py` | DB probability map → quadrilateral boxes |
| `ctc.py` | Logits → text + confidence (**already proven in the spike**) |
| `raster.py` | PDF page → image, image file → array, pixel budget |
| `ppocrv6_dict.txt` | Vendored 18,708-entry dict (Apache-2.0), so only `.onnx` files are user-supplied |

```python
@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]

@dataclass(frozen=True)
class OcrResult:
    lines: list[OcrLine]
    engine: str
    def text(self) -> str: ...
    def mean_confidence(self) -> float: ...
```

**Path resolution order** (never hardcode one Windows path):
`--ocr-model-dir` → `$KE_OCR_MODEL_DIR` → `<repo>/models/ocr/` → `C:/Models/ocr` (Windows)
→ `~/.cache/knowledge-extractor/ocr`. Resolution requires both `.onnx` files by glob
(`*det*.onnx`, `*rec*.onnx`) and explicitly skips the broken `models--*/snapshots/` tree.

**Availability probe** returns a typed reason, so the user is told *why*, not just "no OCR":
missing deps / missing model dir / missing det or rec file / dict mismatch / load failure.
The probe runs once per process and is cached.

**Engine selection:** rapidocr if importable *and* configurable against the local files
with auto-download disabled; otherwise the ONNX engine; otherwise unavailable.
`--ocr-engine {auto,rapidocr,onnx}` forces a choice.

One honest note on that order, given the spike result: the hand-rolled engine is now
*proven* against these exact model files, while rapidocr's compatibility with PP-OCRv6_small
is still unverified and it carries an auto-download path that can quietly break the
offline guarantee. Building both is what you chose and the plan does it — but if rapidocr
turns out to need fighting during Task 1, the right call is to ship the ONNX engine alone
and keep rapidocr as a later addition rather than delay on it. The registry makes that a
one-line change.

**Dict safety:** if `len(labels) != rec_output_classes`, refuse to decode and report
`OCR_MODEL_INCOMPATIBLE` rather than emit plausible-looking garbage.

Riskiest remaining part: **`dbnet_post.py`** (binarize at 0.3 → `cv2.findContours` →
`pyclipper` unclip ×1.5 → `minAreaRect` → score filter). The spike proved detection and
recognition both work; the only observed defect — two columns merged into one line — traces
to the padding stand-in used because `pyclipper` is not yet installed. Getting the unclip
ratio right is the real work here, so this stays isolated in one file with its own unit
tests and a word-gap assertion covering exactly that case.

---

## Task 2 — Model changes

`extractor/model.py`: OCR text is emitted as a **`text` block with provenance**, not a new
`image_ocr` type:

```python
{"type": "text", "content": "...", "source": "ocr", "confidence": 0.94}
```

This is a deliberate change from the old PRD (recovered from git), which specified a
separate `image_ocr` type. That type would be **silently invisible** three ways: the
Markdown writer drops unknown block types, `tests/metrics.py::_all_text` only counts
`type == "text"`, and your Phase 2 chunking would inherit the same blind spot. Reusing
`text` makes OCR content flow everywhere automatically while keeping provenance explicit.

New constructor `make_ocr_text_block(raw_text, confidence)`, running the same
`normalize()` pipeline as all other text. `source`/`confidence` are omitted on non-OCR
blocks, preserving byte-identical output and the existing exact-equality tests.

New warning codes:

| Code | Meaning |
|------|---------|
| `OCR_UNAVAILABLE` | OCR wanted but not runnable; `detail` carries the typed reason |
| `OCR_APPLIED` | OCR ran on this page (page + engine + mean confidence) |
| `OCR_LOW_CONFIDENCE` | Page mean confidence below threshold (default 0.60) |
| `OCR_MIXED_CONFIDENCE` | Page mean passes but individual lines fall below it (count + worst) |
| `OCR_NOISE_FILTERED` | Sub-threshold 1–3 char fragments discarded (count + sample) |
| `OCR_FAILED` | Engine raised on this page; other pages continue |
| `OCR_MODEL_INCOMPATIBLE` | Dict/class-count mismatch — decode refused |
| `OCR_SKIPPED_DISABLED` | Extractable-only-by-OCR content present, `--no-ocr` given |

---

## Task 3 — PDF integration

In `extractor/pdf_reader.py::extract_pdf`, after the existing per-page assembly. The rule
that prevents duplicating natively-extracted text:

| Page class | Action |
|------------|--------|
| `native-text` | Never OCR |
| `scanned` | Rasterize full page, OCR, append as the page's text block |
| `garbled` | Rasterize full page, OCR; **replace** native text when OCR mean confidence ≥ 0.60, else keep native and warn |
| `mixed`, `layout-complex` | OCR **embedded images only** (≥ 50×50 px, and only when the page has any), never the full page — the native text is already correct |

`scanned` and `garbled` never had usable native text, so appending/replacing cannot
duplicate. `mixed` is the only ambiguous case and is handled by never rasterizing the whole
page there. When OCR is unavailable, each such page instead contributes one
`OCR_UNAVAILABLE` warning carrying the page number and reason.

Rasterization is capped by a pixel budget (default ~8 MP, ~300 DPI on A4, degrading DPI on
oversized pages) to bound the 27 MB/frame case measured on landscape slides.

---

## Task 4 — Image files as a new format

`extractor/image_reader.py` registers `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.webp` —
one `section` unit, `page_class: "scanned"`.

`extractor/dispatcher.py` imports every reader eagerly today, so a missing optional
dependency would break `import dispatcher` outright. The image reader is therefore
registered through a **lazy indirection**: the dict value becomes a thin module-level
function that imports the real reader on call. Pillow is already an effective transitive
dependency, so the reader itself always imports; only the OCR engine is optional.

Without OCR, an image file still yields a valid JSON + Markdown pair containing zero text
blocks and an `OCR_UNAVAILABLE` warning — never a crash and never a silently empty file.

---

## Task 5 — Surfacing (the "tell me what you couldn't extract" requirement)

Three channels, because today warnings reach only the JSON:

1. **JSON** — `document.warnings`, unchanged mechanism.
2. **Markdown** — `markdown_writer.py` gains a `## Extraction Notes` section at the end of
   any document that has warnings, in plain language ("Page 4: scanned page, no text
   extracted — OCR engine not available"). Also renders OCR text inline with a
   `> Text recovered by OCR (confidence 94%)` marker so readers know its provenance.
3. **CLI summary** — `main.py::format_summary` gains a warnings roll-up, plus a one-time
   actionable hint when OCR was wanted but unavailable, naming the exact fix
   (`pip install -e ".[ocr]"` and/or where to put the model).

`file_stats()` gains `ocr_pages` and `unreadable_pages` counters.

New flags: `--no-ocr`, `--ocr-engine {auto,rapidocr,onnx}`, `--ocr-model-dir`,
`--ocr-dpi` (default 300), `--ocr-min-confidence` (default 0.60).

---

## Task 6 — Tests

Honors the no-binary-fixtures rule: every fixture is generated at runtime.

- **Synthetic scanned PDF fixture** — write known text with PyMuPDF, `get_pixmap(dpi=200)`,
  embed the bitmap as a full-page image in a fresh text-free PDF. Asserting
  `page_class == "scanned"` verifies the fixture is honest before OCR is asked to read it.
- **Unavailable path (must run everywhere, no model, no marks)** — monkeypatch the registry
  probe to unavailable; assert warnings, Markdown notes, CLI hint, valid image-file output,
  and that non-scanned documents are byte-identical to the `tests/baseline/` snapshot.
- **CTC decode** — unit test against a stubbed logits array; no model needed.
- **DB post-process** — unit test on a synthetic probability map; no model needed.
- **Real-model round trip** — `@pytest.mark.slow @pytest.mark.integration`, auto-skipped
  via `pytest.importorskip` + model-presence check. Asserts ≥ 0.90 phrase retention on the
  synthetic scanned fixture.

CI without the model must stay green: `pytest -m "not slow"` runs the full logic surface.

---

## Task 7 — Packaging and docs

`pyproject.toml`:

```toml
[project.optional-dependencies]
ocr = ["onnxruntime>=1.20,<2", "opencv-python-headless>=4.10", "pyclipper>=1.3,<2", "numpy>=1.26"]
ocr-rapidocr = ["rapidocr>=3.9,<4"]
```

Base install unchanged, so nothing regresses for existing users. Pillow moves from
transitive to explicit, since the image reader now depends on it directly.

README: OCR section (what it does, the two `.onnx` files to supply, where to put them, the
`[ocr]` extra, behaviour without the model), updated format table, new warning codes, and
removal of the stale "OCR is out of scope" paragraph. CHANGELOG entry.

---

## Verification

```bash
# 1. Baseline intact, nothing regressed
python -m pytest -q                      # 140 existing + new tests green
git diff v1.1-pre-ocr --stat             # review scope

# 2. Degradation path — the standalone requirement
set KE_OCR_MODEL_DIR=C:\does\not\exist
python main.py --input input --output out_noocr
#   -> runs clean, JSON/Markdown produced, "Extraction Notes" + CLI hint name the reason

# 3. Explicit off
python main.py --no-ocr

# 4. Real model, real OCR
python main.py --input input --output out_ocr --verbose
python -m pytest -m "slow and integration" -v

# 5. Prove non-scanned output is unchanged by OCR
python -m pytest tests/test_baseline_snapshot.py

# 6. Lint
ruff check .
```

**Acceptance:** a scanned PDF produces real text with confidence recorded; the same repo on
a machine with no model produces the same files minus that text, plus an explicit note in
JSON, Markdown and console saying which pages were not extracted and how to fix it.

## Open item for you

`input/` has no genuinely scanned pages, so step 4 only exercises the synthetic fixture.
Dropping one real scanned PDF into `input/` would make acceptance meaningful.

---

## What changed during implementation

Six deviations from the plan above. Each is a decision the evidence forced, not a
shortcut.

**1. rapidocr was dropped; the ONNX engine ships alone.** Your call, and the plan
already flagged it as the right one if rapidocr needed fighting. The registry keeps
it a one-file addition later. `--ocr-engine` was therefore not added — a flag with
one valid value is noise.

**2. The models ship with the repository.** `models/ocr/*.onnx` (30 MB) are tracked
by git-lfs, so a clone plus `pip install -e ".[ocr]"` runs OCR with no further
setup. This replaced "user-supplied model files" because huggingface.co is blocked
on this network, making any download-on-setup path unusable here. `.gitignore` was
narrowed to allow exactly those two files. An optional `[ocr-hf]` extra plus
`python -m extractor.ocr.fetch_models` covers re-downloading on an open network; it
is never imported by the pipeline.

**3. `pyclipper` fixed the two-column defect the spike hit.** The very first
end-to-end run read all four lines of the spike page, including
`"Owner: HR Team  Date: 2026-01-15"` as one correct line, at 0.993 mean confidence
in 0.30 s. The unclip ratio needed no tuning. `dbnet_post.py` has its own test
asserting a 60 px word gap survives unclip — the exact regression that defect was.

**4. An explicit `--ocr-model-dir` no longer falls back.** The plan's search order
implied a wrong explicit path would silently resolve to `models/ocr/` instead. A
test caught it. Naming a directory now disables the fallbacks, so a typo is
reported rather than papered over.

**5. No lazy indirection in the dispatcher.** It was there to stop a missing
optional dependency from breaking `import dispatcher`. `image_reader.py` imports
only `model` at module level and defers every OCR import to call time, so the
indirection protected nothing and was removed.

**6. The baseline snapshot was generated from the tag, not from current code.** A
snapshot written after the change can only prove self-consistency. It was generated
in a temporary worktree at `v1.1-pre-ocr` and current code is asserted against it,
which proves the real thing. It covers the nine fixtures with no scanned or
image-dominant pages — those are the ones whose output must never change. Scanned
and mixed fixtures are deliberately excluded, since their output is *meant* to
change, and they are covered by the OCR tests instead.

**Two smaller notes.** Markdown output for documents that already emitted warnings
now gains an `## Extraction Notes` section — an intentional change, and the reason
the baseline is JSON-only. And the Windows console was mangling em dashes in
warning text, so `setup_logging` now forces UTF-8 on stdout/stderr.

**7. `layout-complex` pages with embedded pictures now get those pictures read.**
Real-world testing on a screenshot-heavy slide deck (77 pages, PowerPoint-exported)
showed 76 pages classified `layout-complex` — the ruling-line/font-count heuristic
fires easily on diagram- and icon-dense slides — and only 1 page ever reached OCR.
Most of those pages carried real embedded screenshots that were silently never
read. The original rule lumped `layout-complex` in with `native-text` under "never
OCR", but the two classes don't share a rationale: `native-text` means the page
*has no OCR-worthy content*, while `layout-complex` only means *the layout is
visually busy* — it says nothing about whether embedded pictures exist. `mixed`
already proved embedded-image-only OCR is safe (native text is never touched, so
nothing can duplicate); `layout-complex` pages with at least one embedded image
now get the identical treatment. Pages with no embedded images still skip OCR
entirely — nothing to read.

### Still open

The acceptance gap the plan named is unchanged: `input/` has no genuinely scanned
document, so end-to-end verification ran against synthetic fixtures plus a
generated scan. One real scanned PDF would close it.
