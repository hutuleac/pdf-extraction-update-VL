# Code Audit — 31 August 2026

Full-codebase bug hunt against `main` @ `57a82f1`. Baseline before the audit:
`ruff check .` clean, 220 tests pass (`-m "not slow"`). **Every defect below is
invisible to that suite** — none of them fails a test today.

Each finding was reproduced with a probe, not inferred from reading. Probe
output is quoted under "Evidence".

**17 defects total: 10 in the first pass, 7 more in the edge-case second pass
(see "Second pass"), plus 11 secondary items.** Every confirmed defect not yet
fixed has an `xfail(strict=True)` test in `tests/test_edge_cases.py` tagged
with its finding number — the suite stays green, and each marker turns into a
failure the moment its defect is fixed. Current state: **259 passed, 14
xfailed, ruff clean.**

## Status — 1 September 2026

**Findings 1–4 are fixed**, on branch `hotfix/audit-critical-findings`
(commit `2115b9e`), matching the "Hotfix branch" scope from "Suggested
sequencing" below. `tests/baseline` stays green, as predicted. Regression
tests live in `tests/test_cli.py`, `tests/test_new_readers.py`,
`tests/test_ocr_apply.py`, and the new `tests/test_ocr_raster.py` — findings
1–4 never had `xfail` markers in `tests/test_edge_cases.py` (only findings
NEW-1 through NEW-7 and 7 do), so there was nothing to flip there.

**Findings 5–8 are fixed**, on branch `quality/audit-findings-5-8`, matching
the "Quality branch" scope. Regression tests live in
`tests/test_markdown_writer.py`, `tests/test_normalizer.py`,
`tests/test_text_loader.py` and `tests/test_pdf_reader.py`; the four
`xfail(strict=True)` cases for finding 7 in
`tests/test_edge_cases.py::TestCleanInputsProduceNoWarnings` are unflipped and
now pass. Two existing tests asserted the defects rather than the intended
output and were corrected with them: `test_fix_hyphenation_with_space` and the
`hyphenation-space` case in `tests/test_normalizer_extended.py`, both of which
expected `"procedu- ra"` to be rejoined.

Contrary to the prediction below, **`tests/baseline` and `tests/golden` both
stayed green without re-approval** — no corpus document holds a `|` or a
newline inside a table cell, and none has a spaced hyphen. `snapshot.json` did
not need regenerating. Current state: **276 passed, 10 xfailed, ruff clean.**

**Findings 9 and 10 are fixed**, on branch `contract/audit-findings-9-10`
(stacked on the quality branch), closing the "Contract branch" scope.

- Finding 9: `main()` returns 0 (clean, including an empty input directory),
  1 (at least one file failed) or 2 (input directory missing). Documented in
  a new "Exit codes" section in the README and covered by
  `tests/test_cli.py::TestExitCode`.
- Finding 10: **Option B — the flag was deleted, not implemented.** Removed
  `--max-pages`, the `MAX_PAGES` constant, the README default row and the
  `PAGE_LIMIT_EXCEEDED` warning row, and the claim in
  `markitdown-vs-internal-pipeline-comparison.md:43`.
  `test_max_pages_override` became `test_max_pages_flag_is_gone`, which
  asserts the parser now rejects the flag — a guard against someone
  re-adding a documented no-op. The trade-off accepted: `--max-file-mb` is
  now the only input guard, so a pathological page count has no bound.
- Secondary 20 needed no work: `pythonpath = ["."]` is already in
  `pyproject.toml` and a bare `pytest` collects cleanly.

**NEW-1 through NEW-7 are all fixed**, on branch `quality/audit-second-pass`.
Every `xfail(strict=True)` marker in `tests/test_edge_cases.py` is gone —
**the suite now has zero xfails: 291 passed, plus 8 slow OCR integration
tests, ruff clean.**

Two of the prescribed fixes did not survive contact and were changed:

- **NEW-3.** The suggested fix — "remove only the leading run of header lines
  and the trailing run of footer lines" — broke `tests/baseline`. Its premise
  does not hold in this pipeline: the text reaching `remove_lines_from_text`
  is already in *column-aware reading order*, so a footer is not the last
  line. On `two_column.pdf` the real footer stopped being removed and appeared
  twice — once as a footer block, once inside the text. What shipped instead:
  each header drops its **first** occurrence and each footer its **last**,
  with the two bands passed separately (`remove_lines_from_text(text,
  header_lines, footer_lines)`) since position alone cannot tell them apart.
- **NEW-1.** `io.StringIO(text)` alone was not enough. The default
  `newline='\n'` leaves CRLF intact, so a CRLF source yielded
  `'line one\r\nline two'` where a LF source yielded `'line one\nline two'` —
  the same document producing two different cells depending on line endings.
  `newline=None` folds CRLF to LF inside the buffer and is what shipped.

NEW-2 shipped as designed, generalized slightly: banners split the page into
bands rather than being re-inserted by `y0`, so a full-width section heading
part-way down a page works the same as a title at the top.

**The secondary findings are closed too**, on branches
`quality/audit-secondary` and `quality/xlsx-preflight-guard`. **322 passed,
ruff clean.** Every item is fixed:

- **11 — fixed, but not the way the audit proposed.** A row cap with a
  `SHEET_TRUNCATED` warning was rejected: it makes a large workbook silently
  incomplete, the trade-off that removed `--max-pages`. What shipped instead is
  a **pre-flight refusal** — `MAX_WORKBOOK_ROWS` / `MAX_WORKBOOK_CELLS` in
  `limits.py` raise `WorkbookTooLargeError`, so the workbook fails as one file
  the batch reports and steps over, with no content silently dropped. That
  keeps the property the truncation approach would have cost *and* the one the
  do-nothing approach would have cost, because an OOM here is the only failure
  that takes every other file down with it.
  Both caps are needed: rows alone miss a sheet 40 rows by 5,000 columns,
  cells alone accept a million-row sheet one column wide. `_preflight` reads
  each sheet's dimension record — available in read-only mode — so the refusal
  lands before a row is materialized; the running count in `_sheet_rows` is the
  backstop for a sheet with no dimension record or one that understates it.
- **12** `source_type` comes from the real extension, so `.xlsm` says `xlsm`.
- **13** `_iter_shapes` recurses into `MSO_SHAPE_TYPE.GROUP`.
- **14** Slide pictures are OCR'd through `apply.ocr_images`, the same glue the
  PDF and image readers use; unavailability is a warning, as everywhere else.
- **15 — the probe said fix it, and found more than the audit suspected.**
  Both divergences are real and were reproduced: on a 90° page pdfplumber
  reports the table at `(532, 90, 612, 400)` while `get_text("words")` stays
  unrotated, and a MediaBox origin of `(50, 50)` offsets the two spaces by that
  origin. Four of five geometries failed before the fix (upright passed).
  `pdf_reader._to_word_space` translates by the page's pdfplumber origin and
  applies `page.derotation_matrix`; table entries now carry `origin`.
- **16** `MAX_NESTING_DEPTH = 100` in both readers, with a
  `NESTING_TRUNCATED` warning, instead of a `RecursionError` that failed the
  whole file.
- **17** `import fitz` → `import pymupdf` across all seven files.
- **18** The swallowed `get_image_rects` failure logs at debug.
- **19** Contours are sorted by area before the `MAX_CANDIDATES` cap, so the
  cap drops specks rather than real text lines. The first version of this test
  passed vacuously — OpenCV happened to discover the tiny blob last — and was
  rewritten to place it where it is found *first*.
- **21** `setup_logging` closes handlers before dropping them.

One existing test asserted a defect and was corrected with it:
`test_routes_xlsm` expected `source_type == "xlsx"`.

---

## Executive summary

Ten defects change what lands in `output/`. Six of them corrupt or silently
destroy extracted content; four report quality signals that are wrong. The
pipeline's failure isolation and OCR quality logic are sound — the damage is
concentrated in the output layer (naming, Markdown rendering, normalization)
and in three narrow OCR/HTML paths.

Three fixes are urgent because they lose data with no warning at all:

1. **Two input files with the same stem overwrite each other's output.**
   `report.docx` and `report.pdf` both write `report.json` — one document
   disappears from the run with no error and no summary line.
2. **A grayscale embedded image fails OCR for the entire page**, discarding
   every other image's text on that page.
3. **HTML emits every nested text block twice** — `<li><p>` and
   `<blockquote><p>` duplicate their content into the corpus.

Phase 2 inherits all of this: duplicated HTML blocks become duplicated
embeddings, corrupted Markdown tables become corrupted chunks, and the false
`ENCODING_FALLBACK` on every plain-ASCII file makes the warning worthless as a
quality filter.

---

## Key findings

### Finding 1 — the output layer has no collision handling

`write_json` and `write_markdown` derive the output name from
`Path(filename).stem` alone. The source extension is dropped, so any two
supported inputs sharing a stem collide. `discover_inputs` sorts alphabetically,
so the later extension always wins. Nothing in the run summary reveals it: both
files are reported `[OK]`.

### Finding 2 — three OCR paths throw away text they successfully read

The OCR layer is otherwise careful (confidence pooled over lines, noise
filtering reported, unavailability downgraded to warnings). But three paths
discard good output: a grayscale pixmap that never gets converted to RGB, a
single failing image that aborts its whole page, and a garbled page whose
rejected OCR text still reports `OCR_APPLIED` — telling the reader text was
recovered when the document contains none of it.

### Finding 3 — normalization and Markdown rendering corrupt valid content

`_HYPHEN_BREAK` matches any whitespace after a hyphen, not just a line break, so
`well- known` becomes `wellknown`. `_render_table` writes cell text raw, so a
cell containing `|` invents a column and a cell containing a newline destroys
the table. Both silently produce wrong text that reads as if it were correct.

---

## Recommendations — prioritized

| # | Severity | Defect | File | Status |
|---|----------|--------|------|--------|
| 1 | Critical | Output filename collision destroys a document | `json_writer.py:10`, `markdown_writer.py:54` | **Fixed** (`hotfix/audit-critical-findings`) |
| 2 | Critical | Grayscale embedded image fails OCR for the whole page | `ocr/raster.py:63` | **Fixed** (`hotfix/audit-critical-findings`) |
| 3 | Critical | One bad image discards the page's already-read text | `ocr/apply.py:81` | **Fixed** (`hotfix/audit-critical-findings`) |
| 4 | Critical | HTML nested text tags emit every block twice | `html_reader.py:71-96` | **Fixed** (`hotfix/audit-critical-findings`) |
| 5 | High | `|` and newlines in cells corrupt Markdown tables | `markdown_writer.py:15-26` | **Fixed** (`quality/audit-findings-5-8`) |
| 6 | High | Hyphen rejoin merges same-line hyphenated words | `normalizer.py:18` | **Fixed** (`quality/audit-findings-5-8`) |
| 7 | High | `ENCODING_FALLBACK` fires on every plain-ASCII file | `text_loader.py:62` | **Fixed** (`quality/audit-findings-5-8`) |
| 8 | High | Rejected OCR text still reports `OCR_APPLIED` | `pdf_reader.py:256-259` | **Fixed** (`quality/audit-findings-5-8`) |
| 9 | High | Exit code is always 0, even when every file fails | `main.py:249,281` | **Fixed** (`contract/audit-findings-9-10`) |
| 10 | Medium | `--max-pages` / `PAGE_LIMIT_EXCEEDED` documented, never implemented | `main.py:55`, `README.md:228` | **Fixed** — flag deleted (`contract/audit-findings-9-10`) |

---

# Detail, evidence and fixes

## 1. Output filename collision destroys a document — Critical

**Where:** `extractor/json_writer.py:10`, `extractor/markdown_writer.py:54`

```python
stem = Path(model["document"]["filename"]).stem
out_path = out_dir / f"{stem}.json"
```

**Evidence**

```
files written: ['report.json']
surviving content: pdf
```

Two documents in, one file out. The `.docx` extraction is gone. The run summary
reports both as `[OK]`.

**Why it matters:** silent, unrecoverable data loss on a completely ordinary
input set (`spec.pdf` + `spec.docx`). It also breaks the run's own contract —
"one JSON and one Markdown file each".

**Fix.** Compute unique stems once in `main.py`, before processing, and let the
writers accept an override. Collision-free runs keep byte-identical names, so
`tests/baseline/` is unaffected.

```python
# extractor/json_writer.py  (markdown_writer.py takes the same parameter)
def write_json(model: dict, out_dir, *, stem: str | None = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or Path(model["document"]["filename"]).stem
    ...
```

```python
# main.py
def unique_stems(paths: list[Path]) -> dict[Path, str]:
    """Map each input to a collision-free output stem.

    Two inputs sharing a stem ('spec.pdf', 'spec.docx') would otherwise write
    the same output file and one would silently overwrite the other.
    """
    counts = Counter(p.stem for p in paths)
    return {
        p: p.stem if counts[p.stem] == 1 else f"{p.stem}-{p.suffix.lstrip('.')}"
        for p in paths
    }
```

`process_file` then takes the stem and passes it to both writers. If two inputs
share both stem and extension they cannot coexist in one directory, so no
further disambiguation is needed.

---

## 2. Grayscale embedded image fails OCR for the whole page — Critical

**Where:** `extractor/ocr/raster.py:63`

```python
if pixmap.colorspace is None or pixmap.n > 3 or pixmap.alpha:
    pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
```

The guard catches CMYK (`n > 3`), alpha, and missing colorspace — but not
grayscale (`n == 1`), which is what scanned figures and B&W screenshots
embedded in PDFs usually are.

**Evidence**

```
embedded pixmap: n=1 alpha=0 colorspace=Colorspace(CS_GRAY)
raster.py conversion guard triggers? 0   <-- must be True to be safe
array handed to engine.recognize(): shape=(200, 200, 1)
passes engine's (H,W,3) check? False
-> engine raises ValueError -> ocr_images returns OCR_FAILED for the WHOLE page
```

`_pixmap_to_array` slices `[:, :, :3]` on a single-channel buffer, which is a
no-op, so a `(H, W, 1)` array reaches `OnnxOcrEngine.recognize`, which correctly
rejects it — and combined with Finding 3 the whole page reports `OCR_FAILED`.

**Fix.** Test the channel count, not the direction of the mismatch.

```python
# Grayscale (n == 1) needs conversion just as much as CMYK: the engine wants
# exactly three channels, so anything else is converted.
if pixmap.colorspace is None or pixmap.n != 3 or pixmap.alpha:
    pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
```

Add a regression test with a `fitz.csGRAY` pixmap embedded in a synthetic PDF —
`tests/test_ocr_pipeline.py` already builds PDFs this way, and it needs no model
weights.

---

## 3. One bad image discards the page's already-read text — Critical

**Where:** `extractor/ocr/apply.py:76-81`

```python
for image in images:
    try:
        result = engine.recognize(image)
    except Exception as exc:
        logger.warning("OCR failed on page %s: %s", page_number, exc)
        return None, [_with_page({"code": "OCR_FAILED", ...}, page_number)]
```

The docstring says "one bad image must not stop the run", and it does not stop
the *run* — but it stops the *page*, and returns `None`, throwing away every
line already recognized from earlier images.

**Evidence**

```
engine calls: 2
text block returned: None
warnings: ['OCR_FAILED']
-> good text from image 1 was thrown away because image 2 failed
```

**Why it matters:** on a `mixed` page with a photo, a chart and one malformed
logo, all three are lost because of the logo. Combined with Finding 2 this is
the common case, not an edge case.

**Fix.** Isolate per image, exactly as the module already isolates per page.

```python
failures: list[str] = []
for image in images:
    try:
        result = engine.recognize(image)
    except Exception as exc:  # noqa: BLE001 - one bad image must not lose the others
        logger.warning("OCR failed on an image on page %s: %s", page_number, exc)
        failures.append(str(exc))
        continue
    ...

# after the loop, alongside noise_warnings:
if failures:
    noise_warnings.append(_with_page({
        "code": "OCR_IMAGE_FAILED",
        "images": len(failures),
        "detail": failures[0],
    }, page_number))
```

**Use a new code, not `OCR_FAILED`.** `_UNREADABLE_CODES` in `main.py` treats
`OCR_FAILED` as "this page could not be extracted". Once one image can fail
while the others succeed, a page can carry the warning *and* hold text — reusing
`OCR_FAILED` would make the run summary report extracted pages as unreadable.
So:

- `OCR_IMAGE_FAILED` — some images on this page were unreadable, text from the
  rest survives. **Not** in `_UNREADABLE_CODES`.
- `OCR_FAILED` — keeps its current meaning: nothing was read at all. Still
  returned early when `images` is non-empty but every one of them failed.

Add `OCR_IMAGE_FAILED` to `warning_text._TEMPLATES` and the README table.

---

## 4. HTML emits every nested text block twice — Critical

**Where:** `extractor/html_reader.py:71-96`

The walk visits every descendant and emits a text block for any element in
`_TEXT_TAGS`. `li`, `blockquote` and `dd` routinely *contain* `p`, so both the
container and the child match.

**Evidence** — input `<ul><li><p>ITEM ONE</p></li></ul><blockquote><p>QUOTED</p></blockquote>`

```
block: 'ITEM ONE'
block: 'ITEM ONE'
block: 'QUOTED'
block: 'QUOTED'
```

**Why it matters:** every list item written with wrapped paragraphs — the normal
output of most CMS and doc generators — is duplicated in both JSON and Markdown,
and will be embedded twice in Phase 2, double-weighting that content in
retrieval.

**Fix.** Emit only the innermost text element, and keep any direct text a
container holds outside its children.

```python
def _own_text(element) -> str:
    """Text belonging to this element itself, excluding nested text blocks.

    A container such as <li><p>...</p></li> matches _TEXT_TAGS itself and so
    does its child, so emitting both duplicates the content. Inline children
    (<b>, <span>, <a>) are NOT text blocks and must still be included, so this
    walks direct children rather than taking direct strings only.
    """
    if not element.find(list(_TEXT_TAGS)):
        return element.get_text(separator=" ", strip=True)

    parts: list[str] = []
    for child in element.children:
        name = getattr(child, "name", None)
        if name in _TEXT_TAGS:
            continue  # emitted on its own visit
        text = child.get_text(separator=" ", strip=True) if name else str(child).strip()
        if text:
            parts.append(text)
    return " ".join(parts)
```

Then in the walk, replace the `get_text` call with `_own_text(element)`.
`make_text_block` already returns `None` for empty results, so a pure container
emits nothing.

Taking only `find_all(string=True, recursive=False)` here would be wrong: it
drops the content of inline children, so `<li><b>Intro</b><p>body</p></li>`
would lose "Intro" entirely — trading a duplication bug for a loss bug.

---

## 5. `|` and newlines in cells corrupt Markdown tables — High

**Where:** `extractor/markdown_writer.py:15-26`

**Evidence**

```
'| col | desc |\n| --- | --- |\n| a|b | line1\nline2 |'
```

`a|b` becomes two cells against a two-column header; `line1\nline2` splits the
row so the second half is no longer part of the table at all. Both come from
ordinary Excel and CSV content.

**Fix.** Escape at render time — the JSON keeps the raw cell, only Markdown needs
this.

```python
def _cell(value: str) -> str:
    """Make one cell safe for a GFM table row.

    A raw '|' invents a column and a raw newline ends the row, so both are
    neutralised here rather than in the model — JSON keeps the exact cell.
    """
    return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")
```

Escape only what breaks the row. Escaping backslashes as well would rewrite
every Windows path, regex and LaTeX fragment that appears in a table cell —
a much larger change to the corpus than the defect being fixed.

Apply it to header and body cells in `_render_table`. Note this changes golden
output for any corpus document with pipes in a table, which is the point —
`tests/golden` thresholds should be re-approved deliberately.

---

## 6. Hyphen rejoin merges same-line hyphenated words — High

**Where:** `extractor/normalizer.py:18`

```python
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-\s+(?=[^\W\d_])")
```

`\s+` matches a plain space, so the rule fires inside a line, not only across a
line break. The docstring and the comment both describe it as a *line* break
fix.

**Evidence**

```
'procedu-\nra'      -> 'procedura'        (correct)
'well- known'       -> 'wellknown'        (corrupted)
'state- of-the-art' -> 'stateof-the-art'  (corrupted)
'co- operate'       -> 'cooperate'        (corrupted)
```

Spaced hyphens are common in OCR output and in loosely typeset PDFs, which is
precisely the text this pipeline handles.

**Fix.** Require an actual line break. `normalize` runs before line-stripping, so
the newline is still present at this point.

```python
# Only a word broken across a LINE gets rejoined. Matching any whitespace would
# also merge "well- known" into "wellknown", which is a different word.
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-[ \t]*\r?\n[ \t]*(?=[^\W\d_])")
```

Add the three corrupted cases above to `tests/test_normalizer.py` as
regressions.

---

## 7. `ENCODING_FALLBACK` fires on every plain-ASCII file — High

**Where:** `extractor/text_loader.py:62`

```python
confidence = best.coherence if hasattr(best, "coherence") else 0.0
```

`coherence` is charset-normalizer's *language*-detection ratio, not an encoding
confidence. It is `0.0` for pure ASCII, so every clean file scores below the
`0.75` threshold.

**Evidence**

```
plain ascii csv      enc=ascii  coherence=0.000 chaos=0.000 -> FLAGGED ENCODING_FALLBACK
english prose utf8   enc=ascii  coherence=0.000 chaos=0.000 -> FLAGGED ENCODING_FALLBACK
json payload         enc=ascii  coherence=0.000 chaos=0.000 -> FLAGGED ENCODING_FALLBACK
```

End-to-end through the readers:

```
clean.txt   warnings=['the file encoding had to be guessed — detected ascii with confidence 0.00']
clean.csv   warnings=['the file encoding had to be guessed — detected ascii with confidence 0.00']
```

**Why it matters:** the warning appears in JSON, in the Markdown *Extraction
Notes*, and in the run summary for essentially every `.txt`, `.csv`, `.md`,
`.json` and `.html` file. A signal that fires on everything filters nothing, and
it buries the real encoding problems it was built to surface.

Three further problems in the same function: `hasattr(best, "coherence")` is
always true so the guard is dead; lines 60-66 are a comment describing an
approach that was never implemented; and `LoadResult.confidence` is documented as
a confidence but carries a coherence.

**Fix — decide what the warning means first.** Swapping `coherence` for
`1.0 - best.chaos` is *not* sufficient, and shipping it alone would make things
worse. `chaos` is ~0.000 for any file that decoded cleanly, **including a
correctly-detected cp1252 file**, so the warning would then fire essentially
never. Trading a signal that always fires for one that never fires is not a fix.

`ENCODING_FALLBACK` should mean *the decode was a guess*. Two things make it a
guess: the bytes are not plainly ASCII/UTF-8 so a legacy codepage was inferred,
or the chosen encoding still produced implausible sequences.

```python
# charset-normalizer reports `chaos` (ratio of bytes decoding into implausible
# sequences) and `coherence` (how well the text matches a known language).
# Neither is an encoding confidence on its own: coherence is 0.0 for ANY pure
# ASCII file, and chaos is ~0.0 even for a legacy codepage that decoded fine.
# The warning means "we had to guess", so it is driven by both.
MAX_CLEAN_CHAOS = 0.05
CERTAIN_ENCODINGS = ("ascii", "utf_8")

chaos = float(best.chaos)
guessed = encoding not in CERTAIN_ENCODINGS or chaos > MAX_CLEAN_CHAOS
```

`LoadResult.confidence` becomes `1.0 - chaos` and is reported for information;
`guessed` drives the warning. This keeps the cp1252 / latin-1 signal that
`test_latin1_file_detects_encoding` and `test_cp1252_romanian_diacritics`
exercise, and drops only the ASCII noise.

Delete the dead `hasattr` guard and the stale comment at lines 60-66 while
here. `tests/test_text_loader.py` needs a case asserting a clean ASCII file
emits *no* warning, and one asserting a cp1252 file still *does* — the absence
of the first is why this shipped, and the absence of the second is what would
let the over-correction ship.

---

## 8. Rejected OCR text still reports `OCR_APPLIED` — High

**Where:** `extractor/pdf_reader.py:256-259`

```python
if ocr_block and page_class == "garbled":
    if ocr_block["confidence"] >= _min_confidence():
        text_block = ocr_block
    ocr_block = None
```

When confidence is below the threshold the block is dropped, but the
`OCR_APPLIED` warning generated in `apply.py` was already added to
`doc_warnings` on line 252 and stays there.

**Evidence**

```
ocr block confidence: 0.31 | warnings: ['OCR_APPLIED', 'OCR_LOW_CONFIDENCE']
Markdown prints:  - Page 2: text recovered by OCR (confidence 31%)
...while the page's OCR text is nowhere in the document.
```

**Why it matters:** the Extraction Notes tell the reader text was recovered on a
page that contains none. That is worse than saying nothing — it stops them from
going back to the source.

**Fix.** When the block is rejected, replace the claim with the rejection.

```python
if ocr_block and page_class == "garbled":
    if ocr_block["confidence"] >= _min_confidence():
        text_block = ocr_block
    else:
        # The text was read but not trusted enough to publish. Saying
        # "recovered by OCR" here would promise content the page does not hold.
        doc_warnings = [
            w for w in doc_warnings
            if not (w["code"] == "OCR_APPLIED" and w.get("page") == page_number)
        ]
        doc_warnings.append({
            "code": "OCR_REJECTED_LOW_CONFIDENCE",
            "page": page_number,
            "confidence": ocr_block["confidence"],
        })
    ocr_block = None
```

Add the code to `_TEMPLATES` in `warning_text.py`
(`"OCR was too uncertain to replace the damaged text on this page"`), to the
README warning table, and to `_UNREADABLE_CODES` in `main.py` so the summary
counts the page as unextracted.

---

## 9. Exit code is always 0 — High

**Where:** `main.py:249-251, 281`

`main()` returns `None` on every path, including the missing-input-directory
branch, and `sys.exit(None)` is exit code 0.

**Evidence**

```
main() has any 'return <non-None>'?  False
entrypoint: ['sys.exit(main())']
-> sys.exit(None) == exit code 0, always, even when every file fails
```

**Why it matters:** any scheduler, CI step, batch script or the planned desktop
launcher reads this as total success. A run where all 50 files failed is
indistinguishable from a clean one.

**Fix.**

```python
def main(argv: list[str] | None = None) -> int:
    """Discover and process all supported files in input_dir.

    Returns a process exit code: non-zero when the run could not do its job, so
    a scheduler or launcher can tell a failed batch from a clean one.
    """
    ...
    if not input_path.is_dir():
        logger.error("Input directory does not exist: %s", input_path)
        return 2
    ...
    if not files:
        logger.info("No supported files found in %s", input_path)
        return 0
    ...
    for line in format_summary(results):
        logger.info(line)
    return 1 if any(not r.get("ok") for r in results) else 0
```

Add a CLI test asserting the code for a missing directory, a failed file and a
clean run.

---

## 10. `--max-pages` is parsed but never enforced — Medium

**Where:** `main.py:17,55-58`; documented at `README.md:228`

`MAX_PAGES` is imported, `--max-pages` is parsed, `tests/test_cli.py` asserts the
parsed value — and `args.max_pages` is then never read. `extract_pdf` iterates
every page. `README.md` documents a `PAGE_LIMIT_EXCEEDED` warning that no code
can emit; `docs/markitdown-vs-internal-pipeline-comparison.md:43` cites it as a
differentiator.

The launcher plan (`docs/superpowers/plans/2026-08-31-desktop-launcher-ui.md:2745`)
already noted the gap and scoped around it, so this is known — but the README
still promises the behaviour, and the flag still silently does nothing.

**Fix — pick one, do not leave it as is.**

*Option A (recommended): implement it.* It is a stated safety limit, and a
1,000-page PDF currently runs unbounded.

```python
# extractor/pdf_reader.py
def extract_pdf(path: Path | str, *, max_pages: int = MAX_PAGES) -> dict:
    ...
    # inside read_document's loop
    if page_number > max_pages:
        break
```

Emit `{"code": "PAGE_LIMIT_EXCEEDED", "limit": max_pages, "total": doc.page_count}`
once and let the dispatcher pass the option through an
`extractor/ocr/config.py`-style run config rather than widening every reader
signature.

Watch `document.pages`: it currently carries `doc.page_count`, and
`main.file_stats` reports it as the unit count. Truncating units without
changing that field makes the summary claim units that do not exist. Set
`document.pages` to the number of units actually produced and put the true count
in the warning.

*Option B: delete it.* Remove the flag, the `MAX_PAGES` constant, the
`test_cli.py` assertions, the README row and the comparison-doc claim.

**Recommendation, revised:** implement the mechanism but **do not default it to
500**. Truncation converts "this run is slow" into "this document is silently
incomplete", and a 900-page text PDF is a legitimate input, not an attack. Ship
Option A with the limit off by default (`--max-pages 0` / `None` meaning no
cap), so the guard is available when someone needs it and never silently
discards content when they do not. If that is more machinery than the value
justifies, Option B is honest and better than the current state — a documented
flag that does nothing is the worst of the three.

---

# Second pass — edge-case findings

A follow-up exploratory suite (`tests/test_edge_cases.py`) probed the areas the
first pass did not reach. Seven more defects, all reproduced. Each has a
`xfail(strict=True)` test carrying its `AUDIT NEW-n` reference, so the suite
stays green and the marker turns into a failure the moment the defect is fixed.

| # | Severity | Defect | File | Status |
|---|----------|--------|------|--------|
| NEW-1 | High | A newline inside a quoted CSV field is deleted, gluing words together | `csv_reader.py:42` | **Fixed** (`quality/audit-second-pass`) |
| NEW-2 | High | A full-width title collapses two columns and reverses reading order | `reading_order.py:97-136` | **Fixed** (`quality/audit-second-pass`) |
| NEW-3 | Medium | Header removal strips every matching line, including body headings | `headers_footers.py:191` | **Fixed** (`quality/audit-second-pass`) |
| NEW-4 | Medium | Markdown drops table cells past the header width | `markdown_writer.py:24` | **Fixed** (`quality/audit-second-pass`) |
| NEW-5 | Medium | `describe()` raises `KeyError` on an incomplete warning, after extraction succeeded | `warning_text.py:53,58` | **Fixed** (`quality/audit-second-pass`) |
| NEW-6 | Medium | Non-breaking and zero-width spaces survive normalization | `normalizer.py:19` | **Fixed** (`quality/audit-second-pass`) |
| NEW-7 | Low | Nested JSON values are rendered as a Python `dict` repr | `json_reader.py:35` | **Fixed** (`quality/audit-second-pass`) |

## NEW-1 — a newline inside a quoted CSV field is deleted

`extract_csv` feeds `text.splitlines()` to `csv.reader`. Splitting first destroys
the record boundary; `csv.reader` then reassembles the quoted field **without**
the newline it removed.

```
input:  name,note / "Alice","line one⏎line two"
actual: [['name', 'note'], ['Alice', 'line oneline two']]
wanted: [['name', 'note'], ['Alice', 'line one\nline two']]
```

`line oneline two` is a token that exists in no source document. A quoted comma
is handled correctly, so this is specifically the newline path.

**Fix.** Hand `csv.reader` a file-like object so it owns record splitting.

```python
import io
rows = [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
```

## NEW-2 — a full-width title collapses two columns

`_cluster_into_columns` grows each column's span as blocks join it. A banner
title spanning the full text width overlaps both columns, so the first column
created swallows everything.

```
layout: TITLE spans both columns; RIGHT starts 10pt higher than LEFT
actual: num_columns = 1, order = TITLE, RIGHTA, RIGHTB, LEFTA, LEFTB
wanted: num_columns = 2, order = TITLE, LEFTA, LEFTB, RIGHTA, RIGHTB
```

Two failures at once: the reading order is wrong, **and** because
`num_columns == 1` no `POSSIBLE_TWO_COLUMN_ORDER` warning is emitted, so nothing
signals it. Removing the title makes the same page cluster correctly — confirming
the title is the trigger, not the columns.

This is the single most common report layout, and it defeats the module whose
entire purpose is column-aware ordering.

**Fix.** Keep full-width blocks out of the clustering and place them by vertical
position.

```python
# A block spanning most of the text width is a banner (title, rule, footer),
# not a column. Letting it join a column merges every column it crosses.
FULL_WIDTH_FRACTION = 0.8

def _is_full_width(block: Block, page_span: float) -> bool:
    return page_span > 0 and block.width / page_span >= FULL_WIDTH_FRACTION
```

Cluster only the remaining blocks, then re-insert each banner at its `y0`
position in the final order.

## NEW-3 — header removal strips matching body lines

`remove_lines_from_text` filters by exact string match across the whole page.

```
in : 'SPECIFICATION\nIntro paragraph.\nSPECIFICATION\nDetail paragraph.'
out: 'Intro paragraph.\nDetail paragraph.'
```

Both occurrences are gone, including the body heading. Any document whose
repeated header is a plain word — `SPECIFICATION`, `Summary`, a product name —
loses that word wherever it appears as a real heading.

**Fix.** The detector already knows the header lines came from the top band and
the footers from the bottom band. Remove only the leading run of header lines
and the trailing run of footer lines rather than filtering globally.

## NEW-4 — Markdown drops cells past the header width

```python
cells = (row + [""] * len(header))[: len(header)]
```

```
_render_table([["A", "B"], ["1", "2", "3"]])
-> '| A | B |\n| --- | --- |\n| 1 | 2 |'     # "3" is gone
```

Padding a short row is right; truncating a long one loses data. pdfplumber
returns ragged tables routinely.

**Fix.** Size the table to the widest row and pad the header instead.

```python
width = max(len(row) for row in rows)
header = (rows[0] + [""] * width)[:width]
```

## NEW-5 — `describe()` raises on an incomplete warning

```
describe({"code": "OCR_MIXED_CONFIDENCE"}) -> KeyError 'low_lines'
describe({"code": "OCR_NOISE_FILTERED"})   -> KeyError 'sample'
```

`warning_text.py` indexes `warning['low_lines']`, `warning['total_lines']`,
`warning['lowest']` and `warning['sample']` directly. Today's producers always
set them, so this is latent — but `describe()` runs from the Markdown writer and
the CLI summary, *after* extraction succeeded. A malformed warning therefore
destroys a document whose content was already extracted correctly, and
`main.py`'s blanket handler reports it as an extraction failure, pointing the
reader at the wrong cause.

**Fix.** Use `.get()` with sensible fallbacks, and skip the embellishment when
the data is absent. The function already degrades gracefully for unknown codes;
it should do the same for incomplete ones.

## NEW-6 — invisible whitespace survives normalization

`_MULTISPACE = re.compile(r"[ \t]{2,}")` covers only ASCII space and tab.

```
'a\xa0\xa0b'  -> 'a\xa0\xa0b'   (non-breaking spaces kept)
'a​b'    -> 'a​b'     (zero-width space kept)
'before\x00after' -> 'beforeafter'  (control chars are handled)
```

PDFs are full of non-breaking spaces. The same word therefore tokenizes
differently depending on which source it came from, which directly degrades
Phase 2 retrieval — and neither JSON nor Markdown shows a reader anything is
wrong.

**Fix.** Normalize Unicode spaces to ASCII space before collapsing, and drop
zero-width characters outright.

```python
# Zero-width characters carry no meaning and split tokens invisibly.
_ZERO_WIDTH = re.compile(r"[​-‍﻿]")
# Every Unicode space separator becomes a plain space before runs collapse.
_UNICODE_SPACE = re.compile(r"[^\S\n\r\t]")
```

Apply `_ZERO_WIDTH.sub("", text)` then `_UNICODE_SPACE.sub(" ", text)` before
`_MULTISPACE`. Keep `\n` out of the class so line structure survives.

## NEW-7 — nested JSON values become a Python repr

```
[{"a":1,"b":{"x":2}}, ...] -> [['a', 'b'], ['1', "{'x': 2}"], ['3', "{'x': 4}"]]
```

`_records_to_table` calls `str()` on the value. `{'x': 2}` is neither valid JSON
nor readable prose — single quotes, Python spacing.

**Fix.** Render non-scalar values with `json.dumps(value, ensure_ascii=False)`,
so the cell holds `{"x": 2}`. Alternatively exclude records containing nested
values from the flat-record path and let `_nested_to_text` handle them.

---

# Secondary findings

Lower severity — worth a follow-up pass, not a hotfix.

**11. XLSX row accumulation is unbounded.** `_sheet_rows` materializes the whole
sheet into a list before the emptiness check (`xlsx_reader.py:17-22`). XLSX
compresses roughly 10:1, so a 40 MB workbook passes the 50 MB file cap and can
still expand to millions of rows in memory. Add a row cap with a
`SHEET_TRUNCATED` warning, mirroring the page-limit fix.

**12. `.xlsm` is reported as `source_type: "xlsx"`.** `xlsx_reader.py:46`
hardcodes the string. Use `path.suffix.lstrip(".").lower()` so downstream
consumers can tell a macro workbook apart.

**13. PPTX group shapes are never traversed.** `_extract_slide_text`
(`pptx_reader.py:33-45`) iterates `slide.shapes` without recursing into
`GroupShape`, so all text inside a grouped object is lost. Grouping is standard
practice in real decks. Recurse when
`shape.shape_type == MSO_SHAPE_TYPE.GROUP`.

**14. PPTX pictures are never OCR'd.** The PDF reader reads embedded images on
`mixed`/`layout-complex` pages, but `.pptx` inputs get no OCR at all — a
screenshot-only slide produces an empty unit with no warning explaining why.
Either route slide pictures through `ocr_images` or emit a warning naming the
gap.

**15. pdfplumber and PyMuPDF coordinate spaces can disagree.** `extract_tables`
returns pdfplumber bboxes that `reorder_words` tests against PyMuPDF word
coordinates (`table_reader.py:57`, `pdf_reader.py:197`). The two agree for
upright pages with a MediaBox origin at `(0, 0)`, but diverge on rotated pages
and non-zero-origin MediaBoxes — where table text would be duplicated into the
text block instead of excluded. Worth a probe against a rotated-page fixture
before deciding on a fix.

**16. Unguarded recursion.** `_nested_to_text` (`json_reader.py:39`) and
`_render_tree` (`xml_reader.py:24`) recurse without a depth limit. A deeply
nested document raises `RecursionError`, which the batch loop catches — so the
file simply fails. A depth cap with a truncation warning degrades better.

**17. `import fitz` is deprecated.** PyMuPDF 1.28 warns on every import
(`pdf_reader.py:9`, `ocr/raster.py:10`). The pin holds it at 1.28.0 for now, but
the rename to `import pymupdf` is mechanical — do it before the pin moves.

**18. Silent exception swallowing.** `page_signals.py:88-90` catches and `pass`es
with no log line, so a page whose image rects consistently fail is classified on
an `image_area_ratio` of 0 with no trace. Add a `logger.debug`.

**19. `contours[:MAX_CANDIDATES]` truncates arbitrarily.**
`dbnet_post.py:89` keeps the first 1,000 contours in OpenCV's discovery order,
not the largest or highest-scoring, and reports nothing when it truncates. On a
dense page this drops real text lines silently. Sort by contour area first.

**20. Tests only run when the package is installed.** With no root `conftest.py`
the repo root is not on `sys.path`, so a bare `pytest` fails collection on 10
files with `ModuleNotFoundError: No module named 'extractor.ocr'`. A one-line
root `conftest.py` (or `pythonpath = ["."]` under
`[tool.pytest.ini_options]`) removes the trap.

**21. `setup_logging` leaks file handles.** `root.handlers.clear()`
(`main.py:99`) drops handlers without closing them. Harmless in a single CLI
run, a real leak across repeated calls in tests and in the planned long-lived
launcher process. Close them first.

---

# Regression risk: what these fixes would break

Checked against the actual test contracts, not assumed.

**All 220 tests should still pass after all ten fixes — and that is the warning,
not the reassurance.** The suite never covered any of the broken cases:

- `tests/baseline/snapshot.json` holds 9 fixtures — 4 PDF, 2 DOCX, 3 XLSX. No
  `.txt`/`.csv`/`.html`/`.json`/`.xml`/`.md`, so the `text_loader` and HTML
  paths are absent. No images at all, so the OCR paths are absent. It stores
  models, not Markdown, so the table-escaping fix cannot reach it. The only
  hyphen in any fixture is `Sub-punct 2a` — tight, matched by neither the old
  nor the new regex.
- `tests/golden` asserts metric thresholds; no fixture has pipes in cells,
  nested HTML markup, or 500+ pages.
- `test_markdown_writer` asserts exact table rows, but for cells with no pipes.
- `test_cli` calls `main()` and discards the return value, so the exit-code
  change is invisible to it.
- `test_new_readers` has no nested-markup HTML fixture — which is precisely why
  Finding 4 shipped.

**Use the baseline as a tripwire.** By the analysis above, none of the ten fixes
should change those 9 fixtures. If `test_output_matches_pre_ocr_baseline` fails,
a fix reached further than intended — investigate before regenerating.

**Do not regenerate the snapshot to make it green.** `generate.py` builds from
the working tree, not from the `v1.1-pre-ocr` tag, so regenerating always
"passes" and silently erases the guarantee the test exists to hold. Regeneration
must be a reviewed diff with a stated reason.

## Where output quality could genuinely degrade

**1. The grayscale fix (2) can duplicate content — the largest risk here.**
Enabling OCR on images that previously failed means new text appears on `mixed`
pages *beside* native text. Where the embedded image is a screenshot of text
already in the text layer, the page now carries it twice — the same defect
Finding 4 removes from HTML, reintroduced through OCR. No golden test asserts
`duplicate_line_ratio` on the mixed fixture, so nothing would catch it.
**Before merging, measure `duplicate_line_ratio` on a mixed page and add that
assertion to the golden corpus.** If duplication appears, de-duplicate embedded
OCR text against the page's native text rather than reverting the fix.

**2. Fixes that change the corpus, and therefore Phase 2.** Findings 4, 5 and 6
change extracted text for documents currently rendered wrong. That is the
intent, but it means any embeddings or chunk caches built from earlier output
are stale and must be rebuilt, not merged with new output.

**3. Fix 1 changes output filenames for colliding inputs.** `report.pdf`
becomes `report-pdf.json`. Any script or bookmark pointing at the old path
breaks — correctly, since that path previously held whichever document won the
race, but it is a contract change worth announcing.

**4. Findings 3, 7 and 10 each have a wrong version that looks right.** Reusing
`OCR_FAILED`, swapping in `1 - chaos` alone, and defaulting `--max-pages` to 500
would each trade one defect for another. The corrected forms are in the fix
sections above; the naive forms are called out there explicitly.

---

# Suggested sequencing

**Hotfix branch** — Findings 1, 2, 3, 4. These lose data and none of them
changes intended output for correct inputs, so `tests/baseline/` stays green.
**Done** — see "Status" at the top of this document.

**Quality branch** — Findings 5, 6, 7, 8. These change output for documents that
are currently rendered wrong, so `tests/golden` thresholds need deliberate
re-approval and `tests/baseline/snapshot.json` may need regeneration via
`tests/baseline/generate.py`.

**Contract branch** — Findings 9 and 10, plus secondary 20. These fix what the
pipeline promises callers, and the desktop launcher work depends on both the
exit code and a runnable bare `pytest`.

Every fix above should land with the regression test named alongside it. The
common thread in all ten defects is that the suite asserts what the code does
rather than what the output should contain — notably, no test anywhere asserts
that a clean file produces *no* warnings, which is what let Finding 7 ship.
