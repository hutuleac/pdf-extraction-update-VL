# Backlog: Adapt AnyDoc Ideas

## Executive decision

Use AnyDoc as an extension layer, not as a replacement for the main converter.

Keep the main repository's PDF, OCR, VLM, warning, provenance, and JSON pipeline. Adapt AnyDoc's safety, format detection, structural extraction, legacy-format coverage, and testing patterns.

## Priority 0 — Do first

### 1. Add hard resource limits

Adapt AnyDoc's resource controls for:

- ZIP decompression size
- Total archive expansion
- XML depth and node count
- JSON nesting depth
- Image pixel count
- Table row and column expansion
- Merged-cell span expansion
- Embedded asset size and duplication

Reference AnyDoc:

- `anydoc-main/src/package/limits.rs`
- `anydoc-main/src/package/archive.rs`
- `anydoc-main/src/model/table.rs`
- `anydoc-main/src/shared/assets.rs`

Main integration points:

- `extractor/limits.py`
- ZIP/XML readers
- JSON and HTML readers
- Image and table handling

Rule: a safety-limit breach fails the file. Do not silently truncate content.

### 2. Add content-based format detection

Add bounded content sniffing before extension dispatch.

Detection targets:

- PDF signature
- RTF header
- OLE stream names
- ZIP/OOXML package structure
- ODF/EPUB MIME type
- OOXML relationships, content types, and root elements

Keep extension fallback for CSV, TXT, and Markdown, where content signatures are unreliable or absent.

Reference AnyDoc:

- `anydoc-main/src/formats/detect.rs`
- `anydoc-main/src/lib.rs:61-145`

Main integration point:

- `extractor/dispatcher.py`

### 3. Add a centralized fatal-error taxonomy

Distinguish terminal failures from recoverable warnings.

Suggested stable error categories:

- `unsupported`
- `malformed`
- `encrypted`
- `resource_limit`
- `missing_part`
- `io`

Keep the current structured warning codes for degraded but usable output.

Reference AnyDoc:

- `anydoc-main/src/error.rs:11-125`

Main integration points:

- `extractor/dispatcher.py`
- `extractor/limits.py`
- `main.py:174-218`

## Priority 1 — Improve extraction quality

### 4. Preserve richer document structure

Add optional metadata without breaking the current `schema_version = "2.0"` contract.

Candidate metadata:

- Heading level and style
- List level and numbering
- Links and internal anchors
- Formula source
- Footnote and endnote references
- Block provenance

Reference AnyDoc:

- `anydoc-main/src/model/block.rs`
- `anydoc-main/src/model/inline.rs`
- `anydoc-main/src/formats/docx/content.rs`
- `anydoc-main/src/formats/docx/numbering.rs`
- `anydoc-main/src/formats/docx/styles.rs`

Main integration point:

- `extractor/model.py`

### 5. Preserve merged-cell metadata

Extend table blocks with optional metadata for:

- Header rows
- Row spans
- Column spans
- Origin and covered cells
- Table kind: data or layout

Keep the existing `content` rows for compatibility.

Reference AnyDoc:

- `anydoc-main/src/model/table.rs`

Main integration points:

- `extractor/model.py`
- DOCX, XLSX, PPTX, HTML, and PDF table readers
- `extractor/markdown_writer.py`

### 6. Add asset provenance and deduplication

Add optional asset metadata:

- Stable asset ID
- MIME type
- Source package part or relationship ID
- Content hash
- Deduplication status
- Unavailable asset status

Reference AnyDoc:

- `anydoc-main/src/shared/assets.rs`

Main integration points:

- `extractor/model.py`
- `main.py`
- DOCX, PPTX, XLSX, and PDF image extraction

### 7. Improve DOCX structure extraction

Prioritize:

1. Heading levels
2. Numbered and nested lists
3. Hyperlinks and internal anchors
4. Formulas
5. Embedded image provenance
6. Chart and SmartArt text
7. Text boxes and alternate content

Reference AnyDoc:

- `anydoc-main/src/formats/docx/content.rs`
- `anydoc-main/src/formats/docx/numbering.rs`
- `anydoc-main/src/formats/docx/styles.rs`

Main integration point:

- `extractor/docx_reader.py`

## Priority 1 — Testing improvements

Add AnyDoc-inspired tests while retaining all current baseline and golden gates.

Test areas:

- Malformed ZIP and OOXML packages
- Resource-limit enforcement
- Mislabeled file extensions
- Encrypted Office files
- Deep JSON/XML nesting
- Huge merged tables
- Repeated embedded assets
- Asset provenance and deduplication
- Legacy-format expected outcomes
- Deterministic byte mutations
- No-panic and no-hang behavior

Existing test areas:

- `tests/test_dispatcher.py`
- `tests/test_end_to_end.py`
- `tests/golden/`
- `tests/baseline/`
- `tests/metrics.py`

Reference AnyDoc:

- `anydoc-main/tests/snapshots.rs`
- `anydoc-main/tests/robustness.rs`
- `anydoc-main/fuzz/`
- `anydoc-main/bench/`

## Priority 2 — Add AnyDoc as an optional fallback

Support formats currently outside the main repository:

- `.doc`
- `.ppt`
- `.xls`
- `.xlsb`
- `.rtf`
- `.odt`
- `.ods`
- `.odp`
- `.epub`

Create an optional adapter, likely:

- `extractor/anydoc_reader.py`

Architecture:

```text
input
  -> content detection
      -> existing reader for supported formats
      -> optional AnyDoc adapter for unsupported formats
  -> main internal model
  -> JSON writer
  -> Markdown writer
```

Start with unsupported formats only. Keep the current readers authoritative for PDF, DOCX, XLSX, and PPTX.

Map AnyDoc output as follows:

- Heading -> text block plus optional heading metadata
- Paragraph -> text block
- Table -> table block plus optional span metadata
- Image -> image block plus asset provenance
- Formula -> text block with formula source
- Typed AnyDoc error -> main fatal-error taxonomy

## Priority 2 — Improve API and CLI ergonomics

Evaluate:

- Reusable path-based extraction API
- Bytes-based extraction API
- Explicit format override
- Stdin input
- Predictable exit codes
- Diagnostics on stderr

Preserve the existing batch workflow and JSON/Markdown output directories.

## Deferred / do not do now

### Do not replace the PDF pipeline

AnyDoc's PDF path produces Markdown directly and lacks the main repository's:

- Page classification
- Reading-order correction
- Table coordinate transforms
- Repeated header/footer handling
- Local OCR
- Figure OCR
- VLM recovery
- Page-level warnings
- Extraction metrics

References:

- `anydoc-main/src/formats/pdf.rs`
- `anydoc-main/src/lib.rs:121-138`
- `extractor/pdf_reader.py`
- `extractor/ocr/`
- `extractor/vlm/`

### Do not use hosted OCR

AnyDoc's hosted OCR option conflicts with the local-only project constraint. Use OCR-required signals only to route into the existing local OCR workflow.

### Do not replace the JSON model wholesale

The current model is required for page, sheet, slide, OCR, VLM, warning, image, and future chunking provenance.

### Do not make Rust mandatory before measurement

Evaluate, in order:

1. Optional prebuilt Python package
2. CLI subprocess adapter
3. Native binding
4. Source-build integration

Measure Windows installation reliability, startup time, memory, and extraction quality first.

## Recommended implementation order

### Phase 1 — Low risk

1. Content-based format sniffing.
2. Stable fatal-error categories.
3. Archive, image, JSON, HTML, and table safety budgets.
4. Malformed and abuse-limit tests.
5. Preserve all current output.

### Phase 2 — Quality

1. Heading metadata.
2. List metadata.
3. Merged-cell metadata.
4. Asset provenance and deduplication.
5. Better DOCX links, numbering, formulas, charts, and SmartArt.

### Phase 3 — Format expansion

1. Add AnyDoc as an optional dependency.
2. Implement the adapter.
3. Support legacy and currently unsupported formats.
4. Map output into the main model.
5. Add golden tests and benchmarks.
6. Verify packaging on clean Windows installations.

## Success criteria

- Existing baseline and golden tests remain green.
- Existing supported-format output does not change unless intentionally approved.
- Resource-limit violations fail safely and visibly.
- Mislabeled supported files route correctly where detection is reliable.
- New formats produce the same JSON and Markdown contract.
- Every adapted output carries source and warning provenance.
- AnyDoc remains optional and does not introduce runtime cloud calls or downloads.

## TODO — Extraction quality evaluation

### Current run summary

The latest pipeline run on `input/` produced 11 JSON files and 11 matching Markdown files:

- 192 extracted units.
- 289 text blocks.
- 25 table blocks.
- 48 image blocks.
- Approximately 318,620 extracted text characters.
- No invalid block types or malformed JSON output detected.
- No missing JSON/Markdown pairs.

### Quality review priorities

1. Compare 5–10 representative pages from `GSN_STANDARD-VERSION 1.PDF` against the source. It has the highest reading-order risk.
2. Review `WP4_AI_Engineering_Standard_v2_Final.pdf` for section boundaries, lists, tables, and complex layouts.
3. Review `AUMOVIO_FLS36_EMC_Gap_Analysis_vs_Stellantis_CS00244 1.pdf` for table coherence, diagram text, and header/footer removal.
4. Spot-check the four image outputs, especially `3.jpg` and `4.png`, where short OCR fragments were filtered.

### Warning-led investigation

The run reported:

- `LAYOUT_COMPLEX`: 86 pages.
- `POSSIBLE_TWO_COLUMN_ORDER`: 46 pages.
- `MIXED_CONTENT_PAGE`: 11 pages.
- `HEADER_FOOTER_DETECTED`: 5 documents.
- `OCR_APPLIED`: 19 pages.
- `OCR_NOISE_FILTERED`: 2 events.

Treat these warnings as review locations, not as measured error rates. Confirm whether complex-PDF warnings represent acceptable layout complexity or real reading-order defects.

### Success criteria

- Text remains in the correct reading order on sampled multi-column pages.
- Tables preserve row and column meaning.
- OCR text matches the source images for sampled titles, lists, labels, and table-like regions.
- No meaningful figure text is silently lost.
- Intentional quality changes are covered by focused regression tests and baseline/golden review.

---

## TODO — Next, from the 388-page course run (2026-09-16)

Ordered by value per hour of work. Every item below is a measured gap, not a
speculative improvement; the measurement is named so it can be re-checked
rather than trusted.

### 1. Count inline formulas — `markdown_doc._FORMULA` — DONE 2026-09-16

Outcome: PaddleOCR-VL reports **827 formulas against granite's 478**, reversing
that half of the model comparison, and 29 pages previously discarded as
duplicates are kept because their equations now register. Inline formulas are
counted in place, not promoted to display — 338 of 486 sit inside a sentence.
README and CHANGELOG updated. Original entry below.

### ~~1. Count inline formulas — `markdown_doc._FORMULA`~~

`_FORMULA` matches `$$...$$` and `\[...\]`, both *display* delimiters.
PaddleOCR-VL writes most of its maths inline as `\(...\)`: 490 such formulas
on the course run were left raw in the prose, uncounted, and — because counting
and validation are the same pass — never checked by `formula_is_balanced`.

Effect: the `formulas` metric compares delimiter style, not recovery, and any
model comparison resting on it is wrong. It also means PaddleOCR-VL's formulas
skip the balance gate entirely.

Size: one regex plus a test. **Do this first** — it is the cheapest item here
and it invalidates a metric currently quoted in the README.

### 2. A gate for fabricated URLs

Both models invent URLs at a high rate — granite 11 of 13, PaddleOCR-VL 8 of 9
— in well-formed, plausible, wrong form (`jrbengineering.com` arrived as
`thiborgineering.com`). They shipped into the Markdown, and Phase 2 will embed
them as citations.

This is the `formula_is_balanced` problem in a form no balance check sees: a
wrong URL renders perfectly. The obvious shape is to drop or flag a
model-produced URL absent from the page's native text, mirroring the "a wrong
equation that renders is worse than a missing one" rule already in the code.

Open question worth settling before building: drop, or flag and keep? A
`scanned` page has no native text to check against, so a drop rule would be
silent there.

### 3. Re-run PaddleOCR-VL with a raised `--vlm-max-tokens`

Its 143 truncated pages are the entire gap to granite, and on the pages both
models read it was the *more accurate* transcriber. So the default may rest on
a cost artifact rather than a capability one — the two are currently confounded,
and "granite wins" is established only for the shipped 4096-token cap.

Expected outcomes, both one-sentence doc changes: truncation collapses and
formulas climb toward granite's → the default is right for a cost reason;
truncation stays high at 8192 → right for the reason already documented.

Cost: ~2h. The describing pass is cached, so only the reading pass re-runs.

### 4. Native formula debris on formula-heavy pages

PyMuPDF returns some equations' glyphs in spatial disorder — page 115 carries
`∂ ⋅ + ∂ ∂ ∂ + + ⋅` as prose. There is no delimiter or marker distinguishing it
from text, so no rule short of "drop native text where the model produced a
formula" catches it. That is a replace rule and a much larger decision than the
rendering fixes; it needs its own measurement before it is built.

Partially mitigated already: such pages classify `layout-complex`, so `--vlm`
reads them, and the model's `$$` version now renders correctly. The debris
remains beside it.

### 5. Known ceilings — measured, deliberately not fixed

- **The redundancy gate leaks on split diacritics.** granite emits
  `Exist ă ș i`; `apply._words` drops tokens of 3 characters or fewer, so the
  fragments score as novel and the page passes `MIN_PROSE_NOVELTY`. ~21,700
  chars of duplicate prose shipped on 10 of 136 additive pages. The gate works
  on the other 93% (median novelty 1.00), so this is a tail, not a failure.
- **`MISMAPPED_GLYPHS` misses a single stray glyph.** Its threshold is two
  distinct unexpected scripts, which is what keeps an English document quoting
  one foreign word silent. One page in 388 (p117) carries a single Syriac
  character and is not flagged. Lowering the threshold would trade that page
  for false positives on ordinary English input.
- **Private Use Area glyphs need no work.** 291 of 388 pages carry them
  (Symbol/Wingdings bullets and operators), but `normalizer.py` already strips
  them and zero reach the JSON or Markdown. Checked during this audit; recorded
  so it is not re-investigated.

### Re-run notes

The VLM inference cache (`~/.cache/knowledge-extractor/vlm`) holds both models'
readings of the course and the 279 figure descriptions, keyed on the rendered
page, the model, the prompt and the repetition penalty. A re-run that changes
only parsing or warning code costs no inference. Changing a prompt or the
penalty invalidates those entries by design.

The course is 62 MB and needs `--max-file-mb 100`; the 50 MB default silently
skips it.
