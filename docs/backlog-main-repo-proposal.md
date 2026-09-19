# Main Repository Improvement Proposal

## Status (2026-09-19)

- **Phase A: done.** Fatal-error taxonomy (`errors.py`), strong-signature
  validation (`format_detection.py`), JSON/XML/image/table/workbook limits
  (`limits.py`).
- **Phase B: partly done.** DOCX headings and style-based lists are in.
  Still open: native numbering (`numPr`/`ilvl`), hyperlink display text, text
  boxes / `AlternateContent`, OMML equations, and cross-format table quality.
  Measure the gaps on real DOCX files before building anything.
- **Phase C and Phase 3: deferred to Phase 2.** Build them when chunking
  shows a concrete need.
- **Image pixel cap (25 MP):** still refuses rather than shrinks. No file in
  `input/` exceeds it, and PDF pages already lower their DPI to fit. Add a
  resize policy, tested for OCR quality, once a real file hits the cap.

## Scope

This proposal adapts only the valuable ideas from the backlog to the existing
Knowledge Extraction Pipeline.

The current objective is simple:

> Produce as much clean text and clean table content as possible, with visible
> failures and no silent loss of usable content.

This phase is not focused on RAG relationships, rich document semantics, or
asset management. Those can be evaluated later when Phase 2 chunking and
retrieval requirements are concrete.

## Explicitly removed from scope

The following items are not part of this proposal:

- AnyDoc integration, fallback, or legacy-format expansion.
- ZIP decompression and total expanded-size limits for DOCX, XLSX, and PPTX.
- Merged-cell metadata as a new output contract.
- Asset provenance and asset deduplication.
- Relationship-heavy metadata such as internal anchors, footnote links, or
  document-wide asset relationships.
- Replacement of the current PDF pipeline.
- Hosted OCR or any runtime cloud dependency.
- Replacement of the current JSON model.
- A mandatory Rust dependency or native binding.

The existing readers remain authoritative for the formats they already support.

## Product priorities

### Priority 0 — Trustworthy extraction behavior

#### 1. Content-based format validation

The current dispatcher selects readers by file extension. Add bounded content
validation where the file format has a reliable signature:

- PDF file signature.
- RTF header.
- OLE compound-document signature and stream structure.
- ZIP/OOXML package structure.
- ODF or EPUB package MIME information, only if those formats are added later.
- OOXML content types, relationships, and root elements for distinguishing
  DOCX, XLSX, and PPTX packages.

Keep extension-based dispatch as the default mechanism. Content validation must
not silently reinterpret ambiguous files such as CSV, TXT, Markdown, HTML, JSON,
and XML.

Recommended behavior:

- Strong signature matches validate the extension-selected reader.
- A strong mismatch produces a clear malformed or mismatched-format failure.
- Weak or ambiguous content keeps the current extension fallback.
- An explicit format override remains available for controlled recovery cases.

Value: fewer wrong-reader failures, clearer diagnostics, and better handling of
mislabeled Office and PDF files without changing normal output.

#### 2. Stable fatal-error taxonomy

Separate fatal failures from recoverable extraction warnings.

Use stable categories for a file that cannot produce trustworthy output:

- `unsupported`
- `malformed`
- `encrypted`
- `resource_limit`
- `missing_part`
- `io`

Keep the existing structured warning codes for degraded but usable output. Do
not place fatal errors into `document.warnings` when no valid document exists.

Fatal categories should be available in:

- Batch results.
- Console diagnostics.
- Per-file logs.
- A future reusable extraction API.

Value: callers can distinguish an unreadable file, an unsupported format, an
encrypted document, and an operational failure without parsing error strings.

#### 3. Resource limits that protect extraction quality and process stability

Keep resource controls focused on expansion that directly affects the current
pipeline. Do not add ZIP decompression or total archive-expansion budgets.

Evaluate and implement only limits with a clear failure mode and measurable
benefit:

- JSON nesting depth and container size.
- XML nesting depth and node count.
- Image pixel count before rasterization.
- Table row, column, and cell-count expansion.
- Merged-cell expansion only as an internal safety guard, not as output
  metadata.
- Embedded text or asset duplication only where it can cause unbounded memory
  use during extraction.

A safety-limit breach must fail the file visibly. Do not silently truncate text,
tables, pages, rows, or cells.

Each limit needs:

- A documented default.
- A clear unit and enforcement point.
- A test for the breach.
- A test showing that normal large documents still pass.
- A structured `resource_limit` failure.

The repository already refuses oversized workbooks instead of truncating them.
That behavior is the model to preserve.

### Priority 1 — Clean text and clean tables

#### 4. Improve DOCX text extraction

Prioritize changes that improve visible text output without adding relationship
metadata:

1. Heading levels and heading text preservation.
2. Numbered and nested list text in the correct order.
3. Hyperlink display text, without requiring link-target metadata.
4. Formula text where it can be extracted reliably.
5. Text boxes and alternate content when they contain otherwise missing text.
6. Chart, SmartArt, and other embedded text only where corpus testing proves a
   meaningful recovery benefit.

The output should remain clean text blocks. Do not add a broad inline-document
model in this phase.

#### 5. Improve table extraction consistently across formats

Focus on table content quality rather than table relationship metadata:

- Preserve cell text normalization consistently.
- Keep row and column order stable.
- Avoid duplicate table text in surrounding text blocks.
- Handle empty cells predictably.
- Refuse genuinely excessive table expansion instead of producing partial
  output.
- Preserve existing table output compatibility.

Apply improvements first where the current readers already expose tables:

- PDF.
- DOCX.
- XLSX/XLSM.
- PPTX.
- HTML.

Merged-cell handling may improve internal extraction behavior, but it should not
add a new public metadata contract in this phase.

#### 6. Keep native text authoritative unless recovery is needed

The existing PDF, OCR, and VLM design should remain the authority for PDF
extraction.

Do not replace healthy native text with a second representation that mostly
copies it. Continue to use OCR and VLM only where the current page-class and
quality rules show that recovery is needed.

Any new text-recovery behavior must prove that it:

- Adds missing content.
- Does not duplicate native text.
- Does not reduce reading order quality.
- Carries an appropriate warning when confidence is insufficient.

### Priority 1 — Robustness and regression protection

#### 7. Add focused robustness tests

Extend the current test suite while preserving baseline and golden gates.

Test areas:

- Mislabeled PDF and Office files.
- Malformed OOXML and XML structures.
- Encrypted Office files.
- Deep JSON nesting.
- Deep XML nesting.
- Excessive image dimensions or pixel counts.
- Excessive table dimensions.
- Huge merged-cell expansions.
- Repeated or duplicated table content.
- Deterministic byte mutations of supported files.
- No-hang and per-file failure isolation behavior.
- Stable fatal-error categories.

The tests should verify both safety and useful output. A reader that avoids a
crash but silently loses text is still a failure.

Existing baseline and golden tests remain mandatory:

- Baseline snapshots must remain unchanged unless a deliberate output change is
  approved.
- Golden corpus metrics must remain within their existing thresholds.
- New behavior needs targeted fixtures and semantic assertions.

### Priority 2 — Minimal structure for better text quality

#### 8. Add only extraction-relevant optional metadata

Metadata is acceptable when it directly improves text interpretation or output
quality. Keep it small and optional.

Candidates:

- Heading level.
- List level and list marker/type.
- Basic block style where it helps classify headings or lists.
- Source location sufficient to explain a recovered block or warning.

Defer:

- Internal anchors and relationship graphs.
- Footnote and endnote relationship models.
- Asset identity and deduplication.
- Full inline formatting models.
- RAG-oriented breadcrumbs and cross-document references.

Any new field must satisfy all of these conditions:

1. It improves current extraction, reading, or validation.
2. It does not require downstream consumers to understand it to read `content`.
3. It has a clear omission behavior.
4. It has tests across at least two relevant formats, where applicable.

## Recommended implementation order

### Phase A — Safety and diagnostics

1. Define the fatal-error taxonomy.
2. Map current exceptions and batch outcomes to that taxonomy.
3. Add content validation for strong file signatures.
4. Add focused JSON, XML, image, and table safety limits.
5. Add malformed, mislabeled, encrypted, and resource-limit tests.

### Phase B — Extraction quality

1. Measure current DOCX heading, list, hyperlink, and formula gaps.
2. Improve heading and list text extraction.
3. Improve table normalization and duplicate suppression.
4. Improve selected DOCX embedded-text recovery.
5. Add focused fixtures and golden assertions.

### Phase C — Minimal structure

1. Define the smallest optional heading and list metadata shape.
2. Add it only where the reader has reliable source information.
3. Confirm JSON and Markdown remain useful when metadata is absent.
4. Defer relationship and asset models to the later RAG design.

### Phase 3 — RAG-ready structure and relationships

Phase 3 should make extracted content easier to chunk, retrieve, cite, and
validate. It should not turn the extraction model into a broad document graph.
The design principle is **minimum reliable context**, not maximum metadata.

#### 9. Preserve stable hierarchy and chunk boundaries

Represent the document structure that helps a retriever understand context:

- Document identity.
- Unit identity, such as page, section, sheet, or slide.
- Heading path or breadcrumb.
- Block order within the unit.
- Table identity and table position.
- Explicit boundaries between unrelated sections.

Prefer stable structural boundaries over arbitrary character windows. The
extractor does not need to create final embedding chunks, but it should preserve
the information needed for Phase 2 to create them deterministically.

#### 10. Add compact, deterministic provenance

Every retrievable text or table element should be traceable back to its source
without storing unnecessary document metadata.

Recommended provenance fields:

- Stable document ID.
- Unit ID or page/sheet/slide number.
- Block ID based on document structure and order.
- Source type, such as native text, OCR, VLM, or table extraction.
- Optional source coordinates when the reader can provide them reliably.
- Extraction warning references when content quality is degraded.

IDs must be deterministic for the same input and extraction configuration. Do not
use random UUIDs unless they are needed for an external storage layer.

Provenance should support citation and debugging. It should not attempt to
capture every package relationship, formatting detail, or editing history.

#### 11. Model relationships that improve retrieval reliability

Add only relationships that answer a clear retrieval question:

- A block belongs to a unit.
- A block follows another block.
- A block belongs under a heading path.
- A table belongs to a section or unit.
- A continuation block follows a split table or page boundary.
- A recovered block has a source method and warning status.

Avoid a general-purpose relationship graph in this phase. Do not add links,
footnotes, embedded assets, or cross-document references until a concrete use
case and validation strategy exist.

#### 12. Make tables retrieval-safe

Tables need special handling because flattening them into plain text often
removes the meaning of columns and headers.

Phase 3 should preserve only the table context needed for reliable retrieval:

- Table ID.
- Unit and heading path.
- Header rows when they can be identified reliably.
- Row and column order.
- A deterministic textual rendering for retrieval.
- A link from each table representation back to the source table block.

Do not create a second conflicting table model. Keep the current table content as
the source of truth and add retrieval-oriented representations as derived data.

#### 13. Define quality and traceability checks

RAG-ready output needs validation beyond “the parser did not crash.” Add checks
for:

- Every emitted block has a valid parent document and unit.
- Block and table IDs are unique within a document.
- Block order is deterministic.
- Heading paths do not cross unit boundaries incorrectly.
- Tables retain their headers and row order where available.
- Derived retrieval text can be traced to source blocks.
- OCR and VLM content keeps its source and warning context.
- No block is emitted twice through native and recovery paths.

These checks should fail the build or fixture when the invariant is broken.
They should not reject legitimate documents merely because optional metadata is
unavailable.

#### 14. Keep retrieval concerns separate from extraction concerns

The extractor should provide reliable structure and provenance. Phase 2 or a
later RAG layer should own:

- Chunk-size policy.
- Overlap policy.
- Embedding generation.
- Vector storage.
- Hybrid or semantic search.
- Reranking.
- Access control and retention.
- Prompt assembly.

This separation keeps extraction deterministic and reusable outside RAG.

Phase 3 success means that two downstream chunking strategies can consume the
same extracted model and produce traceable, comparable results. It does not mean
that the extractor owns the full retrieval pipeline.

## Decision rules

A proposed change belongs in this phase only if it improves one or more of:

- Clean text coverage.
- Clean table coverage.
- Reading order.
- Duplicate suppression.
- Failure visibility.
- Process stability.
- Deterministic, testable behavior.

A proposed change should be deferred if its main value is:

- RAG relationship construction.
- Cross-document navigation.
- Asset lifecycle management.
- Format breadth without demonstrated user demand.
- Rich styling that does not improve extracted text.

## Success criteria

- Existing baseline and golden tests remain green.
- Existing supported-format output does not change unless intentionally
  approved.
- Resource-limit violations fail safely and visibly.
- Mislabeled strong-signature files produce clear validation results.
- Fatal failures expose stable categories.
- DOCX improvements recover more useful text without duplicating content.
- Tables remain normalized, ordered, and compatible with the current JSON and
  Markdown outputs.
- No runtime cloud calls or downloads are introduced.
- The output stays useful as clean input for the later chunking and RAG phase,
  without prematurely encoding a RAG-specific relationship model.

## Final recommendation

Proceed with the main repository only, in this order:

1. Fatal-error taxonomy and robustness tests.
2. Strong-signature content validation.
3. Targeted safety limits excluding ZIP expansion budgets.
4. DOCX text-quality improvements.
5. Cross-format table-quality improvements.
6. Minimal heading and list metadata only if it improves current extraction.

Keep the current PDF, OCR, VLM, warning, and JSON architecture intact. The next
release should make the pipeline safer and the extracted text cleaner—not turn
Phase 1 into a general document-relationship platform.
