# Do the visual models read tables? (measurement, 2026-09-15)

**Answer: granite does, and it does not matter — pdfplumber ties or beats it on
every page of this corpus, and the model never found a table pdfplumber missed.
PaddleOCR-VL emits no table syntax at all. There is no table gap to close.**

Probe: `scratchpad/table_probe.py`, 9 pages selected for genuine tabular content
(pdfplumber table with ≥3 rows, ≥2 cols, ≥50% cells filled), plus all 6 pages of
the CfR form. Three stages recorded per page: table syntax in the raw model
output, tables after parsing, tables in the emitted document.

## Findings

**1. "Not a single table in any sample" is pipeline suppression, not model
failure.** `pdf_reader.py:505` — `if parsed and not native_tables:` — drops the
model's tables on every page where pdfplumber found one. All 9 tabular pages had
native tables, so zero model tables could reach the output regardless of quality.
The rule is right (below), but it is what the observation was measuring. (This
stage is confirmed by inspection at that line, not by a 388-page run — the other
two stages are measured.)

**2. Granite emits tables; they are never better than native.** Table syntax in
raw output on 9/9 pages, parsed tables on 7/9. Cells recovered vs pdfplumber:

| page | native | granite |
|------|--------|---------|
| Geo 38 | 3t / 72 cells | 2t / 61 |
| Geo 44 | 2t / 51 | 0t / 0 |
| Geo 51 | 1t / 40 | 1t / 30 |
| Geo 58 | 3t / 154 | 2t / 134 |
| Geo 187 | 1t / 56 | 1t / 56 |
| Geo 322 | 2t / 74 | 0t / 0 |
| Geo 79 | 1t / 14 | 1t / 14 |
| Geo 39 | 2t / 17 | 1t / 28 |
| ARMS 15 | 1t / 14 | 1t / 14 |

Ties on the three cleanest tables, loses on four, wins once (Geo 39, where
pdfplumber's two "tables" are figure frames). On Geo 322 — the largest real table
in the corpus, 10×10 — granite emitted `<otsl>` with its bounding box and an
*empty body*: it located the table and transcribed no cells. That is a model
failure, not a parser one.

**3. Granite never recovered a table pdfplumber missed.** Across the 6-page CfR
form: 0 model tables on all 6 pages, including page 3 where pdfplumber reads 2.
Across the 9 tabular pages: no page where native was 0 and granite was not.

**4. `mlx-community/PaddleOCR-VL-1.6-4bit` produced no table syntax on any
page — and the raw output says why.** No `|` runs, no `<table>`; 0/9. On 4 of 9
pages the entire output is one header line (25–52 chars:
`"GEOTEINICĄ – note de curs"`), and 3 more hit the token cap. Not a parser bug —
`markdown_doc.py` parses correctly what it is given, and it is given nothing. Its
table code therefore stays synthetic-test-only, because on this corpus the model
never exercises it.

This is a claim about *that build*, not about PaddleOCR-VL: it is a 0.68 GB 4-bit
quant measured against granite at bf16, and an output that collapses to one line
is as consistent with quantization damage as with model capability. It is not
worth re-running at bf16 — three independent measurements now point the same
way and the decision does not move — but do not cite it as settled fact about
the model.

**5. A table-heavy page can be reported as applied while contributing nothing.**
Separate from the table question, found while tracing stage 3. In `apply.py`, an
additive page whose prose fails `MIN_PROSE_NOVELTY` survives on the strength of
its tables (`if not parsed.text and not parsed.tables: continue` — the tables
save it), and is reported `VLM_APPLIED` with `prose: redundant`. `pdf_reader.py:505`
then drops exactly those tables because native ones exist. The page emits nothing
and is not counted in the `duplicate` tally either. No output is corrupted —
`make_vlm_text_block` returns `None` on empty text, so no empty block is written —
so this is a reporting inaccuracy, not a data bug. On all 9 pages here the native
table existed, which makes this the common case on a table-heavy document rather
than an edge.

**Fixed.** `vlm_pages` now takes the set of pages pdfplumber found tables on, and
the duplicate check asks whether the reader will actually keep the model's tables
rather than whether they exist. Such a page is counted as a duplicate, as it
always should have been.

## What this does not answer

The corpus had no *borderless* tables — the one case where pdfplumber fails
structurally, since it reads ruling lines. Geo 187 and 322 are both ruled. So the
claim was "no table gap on these documents", not "models cannot beat pdfplumber".

**A borderless-table document turned up, and it reverses finding 3 for that
case.** A full-stack run on an 18-page Uponor thermostat manual (2026-09-15,
same day): pdfplumber found 9 tables on 5 pages; granite supplied **15 more on 6
pages where pdfplumber found none** — settings-menu tables, key/icon legends,
value/description pairs, all with correct headers and no ruling lines anywhere
in the document. So:

- finding 3 ("granite never recovered a table pdfplumber missed") holds for
  *ruled* tables and is false for borderless ones;
- the `native_tables`-win rule at `pdf_reader.py:505` is still right — it only
  suppresses where pdfplumber actually found something, so it never blocked
  these;
- the value of the visual model on a document like this is tables, not prose:
  all 6 accepted pages were `prose: redundant` and contributed tables alone.

The remaining untested case is a document with *both* ruled and borderless
tables on the same page, where the suppression is per-page rather than
per-table and would drop the borderless ones.

Only three pages (Geo 187, Geo 79, ARMS 15) are unambiguously tabular with high
native fill; the course's other ~320 `find_tables()` hits are slide layout frames
around figures. Thin evidence, but it points one way on every page.

## Consequence for sequencing

Tables do not re-rank the models. Granite stays the default, the PaddleOCR-VL
build tested stays worse on this corpus for a third independent reason. The
figure-description gap (Qwen3-VL) is the next thing worth building; nothing here
changes what it should be.

It suggested one design that did not survive its own measurement, recorded here
because the reasoning is worth not repeating. Granite's raw output localizes
figures — `<picture><loc_a><loc_b><loc_c><loc_d>` boxes on every illustrated page
probed — so describing each box looked cheaper than a whole-page pass. It is not:
on the reference deck those boxes are 12x11 and 22x19 units (logos) and absent
entirely on two pages that do have figures, and `raster.page_image_regions`
returns zero regions on 16 of its 17 pages, because its figures are vector art
with no embedded raster. Both region sources fail on the half of the corpus that
motivates the feature. `--vlm-describe-figures` reads whole pages.
