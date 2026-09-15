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
