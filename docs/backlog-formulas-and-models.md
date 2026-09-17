# Backlog: formula recognition and model choices

Written 2026-09-17 after the run on the 388-page geotechnics course
(`docs/extraction-findings-2026-09-07.md` and CLAUDE.md hold the earlier
measurements this builds on).

## The problem in one paragraph

Formulas are the one content type the pipeline cannot read without the
visual model. The native text layer holds an equation as one glyph per line
(`g|i|/|sau|100|sap`), symbol fonts decode to unrelated scripts
(`MISMAPPED_GLYPHS`, 21 pages), and PP-OCRv6 reads text lines, not
equations. granite-docling recovers them (478 formulas on the course) but
runs only on Apple Silicon, so on the Windows target every formula page
ships as scattered glyphs. Everything below exists to close that gap with
the least machinery.

## Constraints that decide the answer

- Must run on a CPU-only Windows laptop. Anything mlx-only or GPU-only is
  a Mac convenience, not a solution.
- Local, no downloads at run time. Weights are committed or fetched once.
- Cost is quoted per document, not per page. The course is 388 pages;
  anything above ~1 s per formula-bearing page is a coffee break, above
  ~10 s it is an overnight job.
- A wrong formula that renders is worse than a missing one. Every option
  keeps `doctag.formula_is_balanced` as the gate.

## Candidates

Names are as of mid-2026. Re-check each before starting; this field moves
monthly.

### Formula-only recognizers (crop in, LaTeX out)

| Model | Size | Runtime | Notes |
|---|---|---|---|
| PP-FormulaNet-S | ~40 MB | ONNX / Paddle, CPU | Same family as the OCR already in `models/ocr/`. Also an -L variant. |
| pix2tex (LaTeX-OCR) | ~100 MB | PyTorch, CPU | `pip install pix2tex`. Oldest, simplest API, well-tested on printed math. |
| UniMERNet (tiny) | ~200 MB | PyTorch, CPU | Strongest on handwritten and multi-line; heavier. |

All three need a *cropped formula region*. None finds formulas on a page.

### Region finders

| Option | Cost | Notes |
|---|---|---|
| PyMuPDF block heuristic | none | Blocks whose lines are 1-3 chars, or whose spans use a font that trips `_unexpected_scripts`. Signal already exists in `page_signals` and `pdf_reader`. |
| PP-DocLayout / PP-StructureV3 | ~30 MB ONNX | Layout classes include `formula`. Proper but a second model and a second pre-pass. |
| Docling layout model | torch, ~1 GB+ | Comes with option B below, not worth adding alone. |

### Page-level small VLMs (page in, Markdown + LaTeX out)

| Model | Size | Runtime | Notes |
|---|---|---|---|
| granite-docling | 258M | mlx only here | Current default. Best measured; Mac only. |
| GOT-OCR2 | ~580M | PyTorch, CPU or CUDA | Emits Markdown with LaTeX; runs on Windows, slowly. |
| dots.ocr | ~1.7B | PyTorch, CUDA preferred | Stronger layout; CPU is painful. |
| 3B-7B document models (olmOCR, Nanonets, DeepSeek-OCR) | large | GPU | Out: no GPU on the target. |

### Whole-document engines

Docling (IBM) ships layout, TableFormer tables, OCR, reading order and a
CodeFormula enrichment that returns LaTeX, cross-platform on CPU. About
half of `extractor/` (page_signals, reading_order, headers_footers,
rotated_text, table_reader, most of ocr/raster) is a hand-built subset of
what it does. The trade is a torch dependency of a few GB and the loss of
every tuned behaviour CLAUDE.md documents.

## Plan: experiment before building

Do these in order. Each one has a stop condition. Nothing ships until step
3 passes.

### Step 1: build the formula test set (half a day)

- Take 20 pages of the course: 10 with `MISMAPPED_GLYPHS`, 10 with
  scattered one-glyph lines but no warning (e.g. pages 79-84).
- For each, hand-list the formulas as LaTeX. granite's cached output in
  `.vlm-cache` is a starting draft; correct it by eye against the PDF.
- Store as `tests/golden/formulas/<page>.json`: page number, crop bbox,
  expected LaTeX. This is the benchmark every option is scored on.
- Score = fraction of expected formulas recovered with a normalized
  string match, plus count of *wrong-but-balanced* outputs (the dangerous
  kind).

### Step 2: region finder without a model (one day)

- Throwaway script: from PyMuPDF `get_text("dict")`, collect blocks where
  the median line length is under 4 chars, or any span's font name is
  one `_unexpected_scripts` fires on. Union overlapping blocks.
- Measure against the 20-page set: how many expected formula bboxes are
  covered (IoU > 0.5), how many crops contain no formula.
- Stop if coverage is under 80%: then the layout model is needed and step
  3 gets PP-DocLayout in front.

### Step 3: recognizer bake-off (one day)

- Render each expected crop at 200 DPI. Feed the same PNGs to pix2tex and
  PP-FormulaNet-S on CPU. Record recovery rate, wrong-but-balanced count,
  and seconds per crop.
- Compare to granite's numbers on the same 20 pages from the cache.
- Pick the one with the fewest wrong-but-balanced results, not the highest
  recovery. Ties go to the smaller weights.
- Stop if the best recovers under 60% of what granite does: then the
  answer is GOT-OCR2 on Windows (step 5) and formula OCR is dropped.

### Step 4: integrate the winner (two days)

- New engine under `extractor/ocr/formula_*.py`, glued in through
  `ocr/apply.py` like everything else. Readers do not talk to it.
- Runs only on pages with a formula region from step 2, only when the
  page is not already being read by the VLM (`skip=` semantics from
  `pdf_reader._ocr_pages`).
- Output is a `text` block with `source: "formula-ocr"` holding
  `$$...$$`, gated by `formula_is_balanced`, wrapped by `as_display_math`.
- Warning code `FORMULA_OCR_APPLIED` with a count; rejected outputs under
  `FORMULA_OCR_REJECTED`.
- Weights committed to `models/ocr/` as plain binaries, same as PP-OCRv6.
- README warning table, CLAUDE.md OCR section, `test_ocr_apply.py` with a
  stub engine.

### Step 5: only if steps 2-3 fail

- Try GOT-OCR2 on the Windows box on the same 20 pages. If it is usable
  under ~10 s/page on CPU, add it as a `--vlm-model` entry with its own
  parser, and drop PaddleOCR-VL to keep one non-granite parser.
- If not, evaluate Docling on the same 20 pages plus 5 table pages from
  `docs/table-measurement-2026-09-15.md`. The bar is: at least as many
  formulas as granite, tables no worse than pdfplumber, under 5 minutes
  for the course. Passing all three is the case for replacing the PDF
  path; it is a rewrite, plan it separately.

## Cuts that need no experiment

- Remove PaddleOCR-VL from `models.py` and delete `markdown_doc.py`. It
  truncated on 58% of attempted pages against granite's 5% and has no
  measured win. One parser less to keep the balanced-formula fix in sync.
- Keep `--vlm-describe-figures` off and documented as a four-hour option.
  It is the largest budget in the pipeline and its output cannot be
  verified. Do not spend effort optimizing it before formulas are solved.
