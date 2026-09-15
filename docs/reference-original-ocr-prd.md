> **ARCHIVED / SUPERSEDED — reference only.**
> This is the original OCR PRD written for v.2 and deleted from the working tree in
> commit `d26469d`. It is restored here for context. It assumes **Tesseract**, excludes
> **full-page rasterization**, and specifies a separate **`image_ocr`** block type.
> All three were reversed after investigation — see `docs/ocr-implementation-plan.md`,
> which is the authoritative plan.

# PRD - Faza 1.2: OCR Module

> **Status:** Specified, not implemented.  
> **Dependency:** Phase 1 complete (Tasks 1-12).  
> **Trigger:** `SCANNED_PAGE_NO_TEXT` and `MIXED_CONTENT_PAGE` warnings from Task 5's page classifier.

---

## 1. Scope

OCR covers two distinct input categories:

1. **Standalone image files** — `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`
2. **Embedded PDF images** — images detected inside PDF pages by PyMuPDF

### Known gap

Discrete embedded-image extraction misses full-page scanner output (the entire
page is one image). Page rasterisation (rendering the page as a bitmap then
running OCR) is listed as a follow-on, not part of this scope.

---

## 2. Activation

OCR is **off by default** behind the `--ocr` CLI flag.

```
python main.py --input docs --ocr
```

When `--ocr` is passed but no engine is installed, the pipeline:
- Logs a warning: `WARNING - OCR requested but no engine available`
- Continues processing without OCR (degrades gracefully)

---

## 3. Pluggable engine interface

A thin contract (`OcrEngine`) allows swapping backends without changing callers.

```python
class OcrEngine:
    """Abstract OCR engine contract."""

    def is_available(self) -> bool:
        """Return True if the engine's runtime dependencies are installed."""
        ...

    def extract_text(self, image_path: Path) -> OcrResult:
        """Run OCR on a single image file and return structured output."""
        ...
```

### 3.1 Default engine: pytesseract

- System binary requirement: `tesseract` must be on PATH (or specified via
  `--tesseract-path` escape hatch on Windows).
- Default languages: `ron+eng` (Romanian + English).
- Pure-Python wrapper: `pytesseract` package.
- Lightweight: no ML model download at install time.

### 3.2 Opt-in extra: EasyOCR

- Activated via `pip install knowledge-extractor[ocr-easyocr]`.
- Heavier (PyTorch dependency, model download on first run).
- Better accuracy on complex layouts and low-resolution scans.
- Selected at runtime via `--ocr-engine easyocr`.

---

## 4. Block type: `image_ocr`

OCR results are emitted as a new block type in the internal model:

```json
{
  "type": "image_ocr",
  "page": 5,
  "image_index": 1,
  "image_file": "document_page_005_img_001.png",
  "confidence": 0.91,
  "content": "Status: Approved\nOwner: HR Team\nDate: 2025-01-15"
}
```

Fields:
- `page` — 1-based page number (or 1 for standalone images)
- `image_index` — 1-based index of the image within the page
- `image_file` — filename of the exported image
- `confidence` — average OCR confidence score (0.0-1.0)
- `content` — normalised extracted text

---

## 5. Image export

All processed images are exported to:

```
output/images/
```

Naming scheme:
```
{document_stem}_page_{NNN}_img_{NNN}.png
```

For standalone image files, the original filename is preserved.

---

## 6. Trigger integration

The page classifier (Task 5) already produces:
- `SCANNED_PAGE_NO_TEXT` — image area ratio high, text chars below threshold
- `MIXED_CONTENT_PAGE` — meaningful text plus large image coverage

When `--ocr` is active, the PDF reader checks these classifications and
invokes the OCR engine on the relevant images. Pages classified as
`native-text` are skipped (no OCR needed).

---

## 7. Filtering

Images are skipped when they:
- Contain fewer than 5 printable characters after OCR
- Contain only symbols/punctuation (no alphanumeric content)
- Are smaller than 50x50 pixels (likely icons or decorations)

---

## 8. Normalisation

OCR text passes through the same `normalizer.normalize()` pipeline as all
other text (ftfy repair, hyphenation fix, space collapse, empty line removal).
This ensures consistency with the rest of the extraction output.

---

## 9. Module layout

```
extractor/
└── ocr/
    ├── __init__.py
    ├── image_extractor.py    # extract/export images from PDF pages
    ├── ocr_engine.py         # OcrEngine contract + engine registry
    ├── ocr_normalizer.py     # OCR-specific pre/post processing
    └── ocr_models.py         # OcrResult dataclass, config constants
```

---

## 10. Configuration

```python
# ocr_models.py
OCR_ENABLED = False          # toggled by --ocr flag
OCR_LANGUAGES = ["ron", "eng"]
MIN_TEXT_LENGTH = 5
MIN_IMAGE_SIZE = (50, 50)    # pixels
EXPORT_IMAGES = True
```

---

## 11. Error handling

OCR failures never abort the batch. Per the existing reliability pattern:

```python
except Exception as exc:  # noqa: BLE001 - reliability: isolate OCR
    logger.warning("OCR failed for page %d image %d: %s", page, idx, exc)
```

---

## 12. Dependencies

| Package | Role | Extra |
|---------|------|-------|
| `pytesseract` | Default engine wrapper | `[ocr]` |
| `Pillow` | Image manipulation (already installed) | runtime |
| `easyocr` | Opt-in high-accuracy engine | `[ocr-easyocr]` |

No dependencies are added to the base install. OCR packages live in optional
extras.

---

## 13. Acceptance criteria

- [ ] Standalone `.png`/`.jpg` file extracts text via `--ocr`
- [ ] Embedded PDF images on scanned pages extract text
- [ ] `image_ocr` blocks appear in JSON output
- [ ] Markdown renders OCR text inline (after text blocks, before tables)
- [ ] `--ocr` without tesseract installed logs a warning and continues
- [ ] `--ocr-engine easyocr` uses EasyOCR when installed
- [ ] Images exported to `output/images/` with correct naming
- [ ] Confidence scores are accurate (validated against known-text images)
- [ ] Small/icon images are filtered out
- [ ] Processing time per page is logged at DEBUG level
