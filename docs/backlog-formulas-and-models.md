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

## Benchmark: the 20-page formula set (step 1, built 2026-09-18)

Lives in `tests/golden/formulas/`: one `<page>.json` per page, a
`results/<model>.json` per candidate, `score.py` to compare them, and
`region_probe.py` for step 2. Nothing in it runs under pytest; it is a
measurement, not a regression guard.

### Assumptions the numbers rest on

- **Display formulas only.** An expression standing on its own line, set
  apart from prose, is in scope. Inline maths (`τ_f = (σ − u)·tg φ'` inside
  a sentence, `E_0 = ctg α_0` in a bullet) is not: a crop-based recognizer
  cannot be handed a sentence, and a page-level model already returns
  inline maths as text. 136 formulas across 20 pages.
- **A formula that continues on a second line is two entries** when the
  document breaks it visually (page 79's Δu → h_c chain, page 315's
  p_aC), one when it is one typeset block. Adjacent one-line formulas are
  separate entries even where granite merges them (page 41's η/s pair,
  page 381's p/K pairs); the scorer counts a merged candidate for every
  expected formula it contains, so merging is neither rewarded nor
  penalized.
- **Ground truth is what the page prints, typos included.** Page 115's
  Laplace equation has ∂y² twice in the book; the expected LaTeX has it
  twice. Page 57's `c = V_s/V = 1/V ⇒ e = 1/(1+e)` is transcribed as
  printed. A model that "corrects" the page scores a miss.
- **Notation is not content.** `ν` and `v`, `\left(` and `(`, `27,2^0`
  and `27,2^\circ`, `\tt tg` and `tg`, `_{i}` and `_i`, Romanian glue
  words (`și`, `sau`, `cu`) between two formulas — all folded by
  `score.normalize`. Digits, operators, subscripts and structure are
  compared exactly. A decimal point for the page's decimal comma (`1.85`
  for `1,85`) is folded too: it is a notation choice the reader survives,
  where a changed digit is not.
- **Boxes come from two sources and are not equally good.** 123 boxes
  are granite's own `<loc>` coordinates for the formula it read
  (`bbox_source: granite-loc`); 13 are hand-estimated off a 110-dpi render
  for the formulas granite missed (`estimated`, accurate to ~5 pt). Both
  are used only for the region-finder metric, never for scoring LaTeX.
- **Transcription was one pass, by eye, against a 110-dpi render.** It
  was checked formula by formula and every one of granite's 19 rejected
  outputs was confirmed wrong by hand, but a second reader would be
  cheap insurance before a model is chosen on a margin of a few formulas.

### Page selection

| Half | Pages | Why |
|---|---|---|
| `mismapped` (10) | 41, 59, 79, 82, 88, 95, 111, 141, 146, 381 | Carry `MISMAPPED_GLYPHS`; chosen from the 21 flagged pages for having at least one display formula (24, 61, 86, 99, 142, 211 have none). 78 was skipped: granite hit the token cap there. |
| `scattered` (10) | 34, 57, 73, 81, 115, 150, 209, 315, 329, 384 | No warning, but the native text holds the formula as one glyph per line. Picked from 100 candidates (≥6 lines of ≤3 chars *and* ≥2 granite formulas) to cover definitions, worked examples with numbers (73, 150, 209, 315) and multi-line alignments (115, 384). 315 is in because it holds the wrong-but-balanced equation CLAUDE.md records. |

### Scoring

`python3 tests/golden/formulas/score.py tests/golden/formulas/results/<model>.json`

Per expected formula: **recovered** if its normalized form is a substring
of any candidate's, else **missing**. Per unmatched candidate:
**wrong-but-balanced** if it passes `formula_is_balanced` (it would ship
and render), **unbalanced** otherwise (the existing gate drops it). The
decision metric is wrong-but-balanced, then recovery, then weights —
exactly as step 3 says.

### Baseline: granite-docling (from the cache, `--vlm-pages all`, 4096 tokens, penalty 1.05)

| | mismapped half | scattered half | total |
|---|---|---|---|
| expected | 47 | 89 | 136 |
| recovered | 39 (83%) | 67 (75%) | **106 (78%)** |
| missing | 8 | 22 | 30 |
| wrong-but-balanced | 5 | 13 | **18** |
| unbalanced (gated) | 0 | 1 | 1 |

What the 18 wrong ones are, since the recognizer must beat this list, not
the percentage:

- **Digits changed or dropped** (9): page 209's four regression
  fractions (`(100+200·300)`, `(100+945,83+…)`, `(100+200²)²` for
  `(100+200+300)²`), `27,0^2` for `27,2°`; page 315's `368,10` for
  `36,18`, `19,2·2,04` for `19·1,2·2,04`, a dropped `·K_a1`; page 150's
  `540,405` for `5405,405`. Worked examples with long numeric fractions
  are where it fails.
- **Structure changed** (5): `\sqrt` for `\sqrt[3]` twice (page 34),
  `H^2/H^1` for `H_2/H_1` (page 88), `\vec{\nabla}` for `\vec{v}` (page
  111), `\cos u_\theta` for `\cos\theta_u` (page 79).
- **Half a formula** (2): page 315's p_aC without its numeric line; page
  73's w with one `·100` missing.
- **Renders wrong or not at all** (2): `\kN` (undefined control sequence,
  page 73) and `[kN/m^3 J` for `[kN/m^3]` (page 57). Neither is caught
  by `formula_is_balanced`.

The 30 missing split into 15 granite never emitted (page 111's two
permeability formulas, page 329's three, page 315's two bullet-line
results, the second half of several chains) and 15 that are the wrong
outputs above counted from the other side.

### Region finder without a model (step 2, measured 2026-09-18)

`python3 tests/golden/formulas/region_probe.py` — PyMuPDF text blocks
flagged by any of: median line length under 4 chars, a maths font name,
a script `_unexpected_scripts` rejects; then merged within 6 pt.

| metric | result | stop condition |
|---|---|---|
| expected formulas with a region at IoU > 0.5 | 85 / 136 (62%) | 80% — **not met** |
| expected formulas ≥ 80% inside some region | 124 / 136 (91%) | — |
| regions emitted | 161 on 20 pages | |
| regions holding no formula | 58 (36%) | |

Read together: the blocks *contain* the formulas (91%) but do not
*delimit* them (62%) — a region typically spans two or three stacked
equations plus the "unde:" line between them, and one in three regions is
a bullet list or a short-line caption with no maths at all. For a
recognizer that means multi-formula crops (which granite's merged output
shows is survivable) and 58 crops of prose that will each come back as a
confident, balanced, wrong equation. The literal stop condition fails, so
by the plan step 3 runs with PP-DocLayout in front; the cheaper
alternative worth one more probe is tightening the 6 pt merge and
requiring a maths-font *or* script signal, not short lines alone, to cut
the 36% empties.

### Recognizer bake-off on ground-truth crops (step 3, measured 2026-09-18)

Run crops-first, deliberately skipping the region problem: each of the 136
expected formulas was rendered from its own ground-truth box
(`render_crops.py`, 200 DPI, box widened to the native words it overlaps,
6/4 pt padding) and handed to each recognizer one crop at a time. This is
the recognizers' best case — a perfect region finder, no prose crops — so
a number here is an upper bound on what the integrated path could do.

Environment: PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 (`FormulaRecognition`
pipeline, CPU, needed `tokenizers` and `ftfy` on top of the base install)
and pix2tex 0.1.4 on PyTorch 2.14, both in Python 3.12 venvs; Apple M-series
CPU. Raw outputs are in `results/`, so the table can be regenerated with
`score.py` without the venvs.

| | granite-docling (page) | PP-FormulaNet-S | PP-FormulaNet_plus-S | pix2tex |
|---|---|---|---|---|
| recovered, mismapped half (47) | 40 | 18 | 13 | 18 |
| recovered, scattered half (89) | 66 | 16 | 16 | 18 |
| **recovered, total (136)** | **106 (78%)** | **34 (25%)** | 29 (21%) | 36 (26%) |
| **wrong-but-balanced** | **18** | **47** | 69 | 74 |
| unbalanced (gated) | 1 | 54 | 38 | 28 |
| median s / crop, CPU, uncontended | ~14 s / page | 0.25 | 0.23 | 0.36 |
| weights | 258M, mlx | ~40 MB | ~224 MB | ~100 MB |

**Stop condition hit.** The best crop recognizer recovers 34 of granite's
106, 32% of what granite does, against the 60% bar — and does so while
producing 47 balanced wrong equations to granite's 18. Speed is exactly
as advertised (a quarter second a crop, so the whole course's formulas in
about two minutes), and it does not matter, because the output would
mostly be confident, well-formed, wrong LaTeX.

What the failures look like, from the near-miss list (normalized edit
similarity above 0.85, so these are the *good* cases):

- **Digits and subscripts corrupted on clean crops**: `d_{00}` for
  `d_{60}`, `2!,3` for `2,3`, `\partial_z_z`, `x^{^2}`, `\log` dropped,
  `E_{oed}` losing its interval. These are the same class as granite's
  worst errors, at four times the rate.
- **Runaway tokens**: `\!\!\!\!\!…` runs, `\begin{array}` wrappers around a
  one-line equation, `\stackrel`/`\underset` scaffolding, `\boldsymbol`
  on every symbol. This is the recognizer signalling it does not
  recognise the typesetting — the course is Word Equation Editor output
  with Times italics, not a LaTeX render, and these models are trained on
  LaTeX renders (UniMER, im2latex).
- **Romanian words inside the formula** (`și`, `sau` between two
  equations on one line): every model garbles the word and often the
  formula either side of it. granite reads them as text.

**The watermark is not the cause.** 105 of 136 crops carry the pink
"DIDACTIC" band. Per-crop recovery on the 31 clean crops is 26-29% against
16-21% on the watermarked ones, so the band costs about a third of what
little the recognizers get, and removing it would leave them at under 30%.

**Wider crops did not help.** The first pass used granite's boxes as-is;
widening to overlapping native words moved each model by a few formulas
in either direction (the normalizer was tightened between the two passes,
so the comparison is rough). The models are not starved of pixels.

**Decision: formula-only crop recognizers are out** for this corpus, both
PP-FormulaNet variants and pix2tex, and with them the region-finder work
of step 2 (no recognizer to feed). Two things still stand:

1. granite-docling remains the only measured path to formulas, and it is
   Mac-only. The Windows gap is now a step-5 question: GOT-OCR2 as a page
   model on CPU, or Docling's CodeFormula, on these same 20 pages with
   this same scorer.
2. granite's own 18 wrong-but-balanced formulas concentrate in worked
   examples with long numeric fractions (page 209, page 315). A gate for
   that class — a formula whose numbers do not appear in the page's native
   text — is cheap to test against this set and would catch most of the
   18 without a second model. That is the next experiment before any
   Windows work.

## Parked

- `--vlm-describe-figures` stays off and untouched until formulas are
  solved. It is the largest budget in the pipeline (~45 s/page, 330 of 388
  pages on the course) and its output cannot be verified. No optimization
  work on it before step 4 above ships.

## Decision pending: PaddleOCR-VL

An earlier draft of this file listed removing PaddleOCR-VL and
`markdown_doc.py` as a cut needing no experiment. That was written from
CLAUDE.md's stale count (57 formulas) and is wrong. The README's later
page-level diff of the full course run says:

- PaddleOCR-VL recovered 827 formulas against granite's 478, on fewer
  pages (131 kept against 170), once inline `\(...\)` maths was counted.
- On the 50 pages both models kept, PaddleOCR-VL is the more accurate
  transcriber; granite shipped a balanced but wrong equation on page 315.
- granite splits Romanian words around diacritics 848 times across 28
  pages; PaddleOCR-VL zero times.
- PaddleOCR-VL loses on coverage only because the 4096-token cap binds on
  58% of its pages. Raising `--vlm-max-tokens` for it is untested.

So it is the better transcriber and the worse coverage, and the coverage
loss had an untested one-flag fix. That experiment ran on 2026-09-18.

### Result: the 8192-token run (`--vlm-pages auto`, 56 damaged pages)

| | PaddleOCR-VL, 8192 tokens | granite-docling, 4096 |
|---|---|---|
| pages kept | 19 | 48 |
| rejected as truncated | 28 | 3 |
| display formulas kept | 49 | 185 |
| inline formulas kept | 215 | 168 |
| prose chars kept | 31,700 | 74,590 |
| Romanian words split at a diacritic | 0 | 293 |
| pages falling through to OCR | 24 | 2 |
| wall time | 27 min (~29 s/page) | cached; ~14 s/page measured earlier |

Doubling the cap changed nothing: 28 of 56 pages still hit it, and the
capped outputs run 8k to 24k characters, far beyond what a page holds. Ten
of the 28 end in a literal loop (`V_p(w=0) +1.1` repeated to the cap); the
rest are runaway generation of another kind. The cap is a symptom. The
model is built as the element-recognition stage behind PP-DocLayoutV2 and
is being fed whole pages, which its own card does not describe. No
sampler setting fixes that.

What it still does better, on the pages it finishes: cleaner LaTeX
(`\text{kN/m}`, `\operatorname{tg}`, no per-character spacing) and zero
diacritic splitting. granite's `p ă mânt` for `pământ` appeared 293 times
on 48 pages. That is a granite defect, but it is a five-line post-fix in
`doctag.py`: a lone `ă â î ș ț` between two word characters is never a
word in Romanian, so it can be joined back.

**Decision, applied 2026-09-18: PaddleOCR-VL removed from the pipeline** (model table entry, `markdown_doc.py`, its tests). Coverage is the larger
effect by far and its one real advantage is fixable on granite's side. The
model itself is not the problem; whole-page prompting is. Its correct use,
the official two-stage `PaddleOCRVL` pipeline in PaddleOCR 3.x (layout +
element recognition, PyTorch or Paddle, CPU or CUDA, Windows), is the
candidate for step 5 above, and is the only route by which this model
comes back.
