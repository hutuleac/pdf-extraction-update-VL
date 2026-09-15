# Spike — granite-docling-258M on `Geotehnica - note de curs.pdf`

Date: 2026-09-05. Throwaway probe, no pipeline code changed.
Question: **does granite-docling-258M produce output worth building a
subsystem around?** Answer: **yes, as a routed fallback — never as a
replacement.**

## Setup

- `ibm-granite/granite-docling-258M-mlx` via `mlx-vlm` 0.6.17 (needs
  `torchvision` for the Idefics3 image processor).
- 9 pages rendered at 250 DPI with PyMuPDF (`fitz.csRGB`), 2067x2924 px.
- Prompt: `Convert this page to docling.`, `max_tokens=4096`.
- Host: M-series Mac, 26 GB. Model load 0.8 s.
- Probe scripts + raw output: session scratchpad (`render.py`, `probe.py`,
  `granite_out/`). Not committed — throwaway.

## Timing

| page | seconds | chars out |
|---|---|---|
| 006 | 6.4 | 330 |
| 015 | 6.0 | 2116 |
| 017 | 4.3 | 1403 |
| 018 | 32.8 | 10765 |
| 020 | 32.2 | 10256 |
| 046 | 9.8 | 3825 |
| 058 | 13.8 | 4742 |
| 073 | 16.8 | 5092 |
| 348 | 5.6 | 2008 |

Mean 14.2 s/page. Whole document (388 pp) ≈ 92 min.

**Routed-subset size, measured after the table fix (Phase 1.4):** text-deficient
pages (<200 native chars) fell from **169 to 15**, and only 3 of those are not
`native-text`. Adding the 35 `garbled` pages — which now *have* native text but
whose formulas are destroyed, so character count cannot judge them — the queue is
**38 pages, ≈9 minutes**, not the 216 pages / 51 min a pre-fix estimate would have
given. Fixing tables first shrank the VLM's job by 82%. Caching is still worth it
but is no longer the difference between usable and not.

## Per-page verdict

| page | why chosen | current pipeline | granite | verdict |
|---|---|---|---|---|
| 073 | `garbled` | formulas flattened to token soup: `M 185 1,85 g/cm³ V 100 V G M·g` | **valid LaTeX** for every formula, correct values | **big win** |
| 058 | real tables + formulas | prose box swallowed the page | correct OTSL table (headers, `S$_r$` subscripts, footnote) + 2 correct LaTeX formulas | **big win** |
| 017 | formula fragmentation | OCR gave `>4 3 S 11<10< >5 >8 12<` | full legend list 1–12 correct, diagram marked `<picture>` with bbox | **big win** |
| 046 | OCR conf 0.0 | prose trapped in a table cell | correct structure: headers, `<unordered_list>`, subscripts as `$_{L}$` | **win** |
| 348 | OCR conf 0.0 | thin | correct headers, list, `<picture>`, `F$_s$` | **win** |
| 015, 018, 020 | thin + many images | prose in table cells, reversed URLs leaking | full prose recovered, images as `<picture>` | **win** |
| 006 | **native-text control (TOC)** | 4556 chars, perfect | **330 chars — emitted an empty `<otsl>` and stopped** | **catastrophic regression** |

## Findings

1. **Formulas: solved.** `<formula>` tags carry usable LaTeX
   (`\frac{G_w}{G_s}\cdot 100`), including on the `garbled` pages where the
   native path produces unusable output. This is the capability gap the last
   handoff described, closed.
2. **Tables: OTSL output is good** — column headers, spans (`lcel`), subscripts,
   footnote markers all preserved on page 58.
3. **Watermark: no leakage.** The "DRAFT" diagonal stamp appears on every
   rendered page and never once appeared in the output. **The addendum's
   watermark-masked inference render is not needed** — drop it from the plan
   until a page proves otherwise.
4. **It hallucinates words.** `imiditatea`→`umiditatea`, `mâlcurilor`→`mâlurilor`,
   `Caracterizaerea`→`Caracterizarea`, `Aniculãesi`/`Aniculāesi`→`Aniculăesi`.
   Confirms the addendum's precedence rule: **native text stays authoritative**,
   Granite never overwrites it.
5. **It fails silently on some layouts.** Page 6 (a table of contents with dot
   leaders) was classified as a table and returned an empty `<otsl>` — a 93%
   content loss on a page the native path handles perfectly. **Never route a
   `native-text` page to Granite, and treat a large drop in extracted characters
   vs. the native pass as a rejection signal.**
6. **Windows path is a candidate, not an answer.** `onnx-community/granite-docling-258M-ONNX`
   exists and the repo already depends on `onnxruntime`, but VLM ONNX exports
   normally ship as several graphs (vision encoder / embedder / decoder with KV
   cache) needing their own glue — that is not a drop-in for a detection+recognition
   pipeline. Needs its own probe before the platform question is settled.
7. **Output is `doctag`, not Markdown.** Granite emits `<doctag>`, `<otsl>`,
   `<formula>`, `<loc_*>`. Converting that into this repo's `make_text_block` /
   `make_table_block` model is real, unscoped work — either a new `docling-core`
   dependency or hand-parsing the tag subset. For the RAG consumer, that
   conversion is where the value actually lands.

## What this changes in the addendum's plan

**Drop:** watermark-masked inference renders (finding 3) and `WATERMARK_MASK_APPLIED`.
**Drop for v1:** KaTeX/MathJax render-validation (needs Node). Granite's LaTeX was
syntactically clean on every sample; a bracket-balance check is enough to start.
**Add:** a rejection rule based on output-vs-native character count (finding 5).
**Confirm:** full-page render, not per-image crops. Routing by existing page class.

## Blocking prerequisite found while probing

Independent of any VLM: **124 of 282 table blocks (on 124 distinct pages, 32% of
the document) are not tables.** `extractor/table_reader.py::_is_real_table` only
checks ≥2 rows and ≥2 columns; these slides have a grey title bar over a content
box, which pdfplumber's line detection reads as a 2x2 grid, so whole slide bodies
land in a single markdown cell joined by `<br>`. 289K of 729K text chars.

The separation is clean and bimodal:

| | median non-empty cell length | median newlines per cell |
|---|---|---|
| real tables (p332, p291, both on p58) | 2–14 chars | 0–1 |
| prose boxes (p17, p18, p46, p57, …) | 200–1900 chars | 11–37 |

`median length >= 120` and `median newlines >= 3` each flag exactly 124 blocks
and agree on 122. The two the newline rule flags alone have short cells (37 and
18 chars) — plausibly real tables with wrapped text. **Use median cell length
alone**; OR-ing the two only adds false positives.

`# ponytail: threshold calibrated on one slide-deck PDF; a spec table with a
long Notes column could trip it.`

### Verified, not assumed

`pdf_reader.py:346` builds `exclude_regions` from the entries that survived
`_is_real_table`, so a rejected detection also stops excluding that area from the
PyMuPDF text pass. Probed by monkeypatching the guard and re-running `extract_pdf`
on 11 pages:

| pdf page | before | after |
|---|---|---|
| 17 | 1 table, 20 text chars | **0 tables, 848 chars of prose** |
| 46 | 1 table, 34 chars | **0 tables, 2593 chars** |
| 58 | 3 tables, 37 chars | **2 tables (both real ones kept), 910 chars** |
| 15 / 18 / 20 / 42 / 194 | 1 table, 34–46 chars | **0 tables, 594–1607 chars** |
| 73 / 291 / 332 | unchanged | **unchanged** (0, 1 and 6 real tables intact) |

Recovered prose has real newlines, not `<br>`.

**Confirmed on the full 388-page run** (native text only; OCR models are
LFS pointers on this machine, and this change does not touch the OCR path):
native text 342,430 -> 614,741 chars (+80%), table blocks 282 -> 137,
prose boxes 124 -> 0, reversed-URL cells 11 -> 1, **0 pages lost >100 chars**,
156 pages gained >100. The 145 dropped detections are the 124 prose boxes plus
21 entirely blank grids. Shipped as Phase 1.4.

**The reversed-URL leak fixes itself.** All five pages that carried it
(15, 18, 20, 42, 194) come back clean once their prose moves to the PyMuPDF text
path — the word-level rotated-text filter there drops the 90°-rotated photo
credits that the char-level table path let through. It is a *symptom* of the
table bug, not a second defect. No separate fix needed.

Remaining minor issue seen in the recovered text: `\uf0e8` private-use glyphs
(Wingdings bullet arrows) survive normalization on page 20.

## Trap for the eventual regression tests

Printed page numbers are not PDF indices here, and not by a constant offset —
PDF page 46 prints "34", while PDF page 17 matches the handoff's description of
"page 17". The addendum's golden pages (17, 46, 57) are ambiguous; page 57's
"Kaolinit table" is in fact a prose box. **Pin every regression page by PDF index
and look at the render before writing the assertion.**

## Follow-up sweep: all 35 `garbled` pages

The queue is 92% `garbled` pages, and the original evidence for that class was a
single page (73). Re-ran the probe over the other 34 (~8 min, model already
cached) to check whether the page-6 failure mode hides inside the set we plan to
route.

**It does not.** Not one page returned less than 95% of its native character
count; most returned 1.2-1.7x. **129 formulas recovered across 34 of the 35
pages.** No silent truncation anywhere in the routed set.

Two real failure modes did show up, both detectable without a renderer:

1. **Token-cap truncation — 10 of 35 pages.** Output does not end in
   `</doctag>`; generation hit `max_tokens=4096`. On pages 176 and 357 a
   `<formula>` was left unclosed. Page 176 is the clear case: one complex Egorov
   formula degenerated into a repeating `\left[ \frac{z_i}{b} ...` fragment that
   consumed the entire budget. Raise the cap and re-measure before treating this
   as a model limit — 4096 was an arbitrary probe setting.
2. **Unbalanced `\left`/`\right` — 6 of 127 formulas (95% clean).** Page 175 has
   all 5 of its formulas unbalanced despite terminating normally, so this is not
   only a truncation artifact.

### What this settles for the merge policy

Rejection needs exactly two cheap checks, no Node and no KaTeX:

- output does not end in `</doctag>`, or any tag is left unclosed -> reject the page
- a formula's `\left`/`\right` counts disagree -> reject that formula, keep the page

Combined they would have caught every defect in this sweep. The character-ratio
floor is still worth keeping for the page-6 case, but note it never fired on a
`garbled` page — it guards the *routing* mistake (sending a healthy page), not
the model.

### Routing rule

`page_class == "garbled"` OR (fewer than 200 native chars AND not `native-text`).
35 + 3 = 38 pages. That is just "the pages `page_signals` already flags as
damaged" — no new detector, no formula/chart/diagram classifier, nothing like the
addendum's eight-condition list.
