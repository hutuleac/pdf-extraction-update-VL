# markitdown vs. internal Knowledge Extraction Pipeline

Comparison date: 2026-08-31

Repos compared:
- `microsoft/markitdown` (installed via `uv tool install "markitdown[all]"`)
- `D:\Claude\pdf-csv-xlsx-ppt-word-Convert-to-Text-v.2` (internal, "Knowledge Extraction Pipeline — Faza 1")

Test files: `D:\Claude\PDF-to-markdown\input` (3 PDFs, 1 xlsm).

## Executive summary

The internal repo is a purpose-built, quality-aware extraction pipeline for a RAG project; markitdown is a general-purpose "dump to markdown" converter. For this document set (mixed native + scanned PDFs, merged-header Excel sheets), the internal tool produces materially cleaner, more structured output — worth keeping over markitdown for this use case.

## Differences by format

### PDF
- Internal: PyMuPDF for text + pdfplumber for tables, table regions excluded from the text stream so content isn't duplicated. Adds column-aware reading order (fixes two-column mis-ordering), repeated header/footer detection and stripping, and page classification (`native-text` / `scanned` / `mixed` / `garbled`) with explicit warnings.
- markitdown: pdfminer.six, single-pass linear text extraction. No table/text de-duplication, no column awareness, no scanned-page detection — a scanned PDF just silently returns near-empty text.
- Verdict: internal tool is meaningfully better for real-world PDFs. The CEF slide deck test file came out with table-artifact garbling under markitdown.

### Excel
- Internal: raw openpyxl (`data_only=True`, `iter_rows`), one table block per sheet, blank cells → `""`. No pandas involved.
- markitdown: pandas + openpyxl — this is exactly why `QDM_McLaren_RFQ.md` came out with `Unnamed: 0`, `NaN`, and merged-header noise: pandas tries to infer a header row and fails on multi-row/merged headers.
- Verdict: internal reader avoids the NaN/Unnamed problem entirely by treating cells as plain values.

### CSV
- Internal: stdlib `csv` with delimiter sniffing and charset-normalizer encoding detection.
- markitdown: pandas `read_csv` with default delimiter/encoding assumptions — more likely to mis-parse European CSVs (`;` delimiters, non-UTF8 encodings).

### Word (.docx)
- Internal: python-docx directly, preserving document order of paragraphs and tables as separate block types.
- markitdown: mammoth (docx → HTML → markdown). Handles rich formatting (bold/headings) better, but tables and paragraph order can get flattened/reordered on complex documents.

### HTML
- Internal: BeautifulSoup + lxml, scripts stripped, headings/paragraphs/tables mapped to explicit block types.
- markitdown: markdownify over BeautifulSoup — comparable quality; internal tool is more explicit about what's excluded (scripts).

## Structural differences

1. **Format-agnostic internal model** — every reader (PDF, xlsx, docx, html, pptx, json, xml…) emits the same `document → units → blocks` schema (text/table/header/footer). markitdown has no intermediate structured representation — converter-per-format straight to a markdown string.
2. **JSON output alongside Markdown** — machine-readable output for downstream RAG chunking/embeddings. markitdown only produces one output shape.
3. **Quality warnings baked in** — `SCANNED_PAGE_NO_TEXT`, `GARBLED_TEXT`, `POSSIBLE_TWO_COLUMN_ORDER`, `HEADER_FOOTER_DETECTED`, `ENCODING_FALLBACK`, `UNICODE_REPAIRED`, and the OCR outcome codes. markitdown gives no signal when extraction quality is poor.
4. **137 tests + golden corpus** with semantic metric threshold assertions, tuned to this document set. markitdown has its own test suite but nothing tuned to these formats/content.
5. **Broader format coverage on the input side**: internal tool also treats JSON, XML, TXT, MD as first-class inputs, not just office/PDF formats.

## What markitdown has that the internal tool doesn't

- Broader long-tail format support out of the box (audio transcription, YouTube, EPUB, images via OCR/vision if configured, Outlook `.msg`).
- Backed by Microsoft, actively maintained, larger community — lower maintenance burden.
- Legacy `.doc` / `.xls` / `.ppt` support (internal tool explicitly excludes these — out of scope for Phase 1).

## Recommendation

Use the internal pipeline as the primary path for PDF/CSV/XLSX/DOCX/HTML — it's directly tuned to avoid the exact failure modes markitdown demonstrated on this test set. Reserve markitdown for formats the internal tool doesn't cover (legacy `.doc`/`.xls`, audio, or anything needing quick ad hoc conversion without touching the internal codebase).

## Reference: internal pipeline architecture

```
input/*.{pdf,csv,docx,xlsx,txt,md,html,pptx,json,xml}
   |
   v
dispatcher.extract_document(path)
   |
   +-- .pdf  -> pdf_reader     (PyMuPDF text + pdfplumber tables + signals)
   +-- .csv  -> csv_reader     (charset-normalizer + stdlib csv)
   +-- .docx -> docx_reader    (python-docx)
   +-- .xlsx -> xlsx_reader    (openpyxl)
   +-- .txt  -> txt_reader     (charset-normalizer)
   +-- .md   -> md_reader      (charset-normalizer, pass-through)
   +-- .html -> html_reader    (BeautifulSoup + lxml)
   +-- .pptx -> pptx_reader    (python-pptx)
   +-- .json -> json_reader    (stdlib json)
   +-- .xml  -> xml_reader     (defusedxml)
   |
   v   (internal model, via model.py constructors)
   +-- json_writer     -> output/json/<name>.json
   +-- markdown_writer -> output/markdown/<name>.md
```

Source: `D:\Claude\pdf-csv-xlsx-ppt-word-Convert-to-Text-v.2\README.md`, `extractor/pdf_reader.py`, `extractor/xlsx_reader.py`.
