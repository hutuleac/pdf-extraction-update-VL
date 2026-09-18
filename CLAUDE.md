# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Knowledge Extraction Pipeline (v.3, "Phase 1"). Converts local documents (PDF,
CSV, DOCX, XLSX/XLSM, TXT, MD, HTML, PPTX, JSON, XML, images) into one JSON and
one Markdown file each. Runs 100% locally — no cloud calls, nothing uploaded or
downloaded at run time. Output feeds a future Phase 2 (chunking + embeddings);
every format is normalized into the same internal model so Phase 2 stays
format-agnostic.

v.3 continues v.2 and adds OCR (scanned PDF pages + image files). v.2's git
history is preserved. See `docs/ocr-implementation-plan.md` for the OCR design
and `tests/baseline/` for the pre-OCR output snapshot (tag `v1.1-pre-ocr`,
commit `d26469d`) that every run is still asserted against.

## Commands

```bash
# Setup (editable install, dev deps: pytest, pytest-cov, ruff)
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

# Run the pipeline (defaults: input/ -> output/, log to logs/extraction.log)
.venv/Scripts/python.exe main.py
.venv/Scripts/python.exe main.py --input docs --output out --max-file-mb 5 --verbose

# Full test suite with coverage
.venv/Scripts/python.exe -m pytest --cov=extractor

# One test file / one test
.venv/Scripts/python.exe -m pytest tests/test_dispatcher.py -v
.venv/Scripts/python.exe -m pytest tests/test_dispatcher.py::test_routes_xlsm -v

# Golden corpus only (semantic metric assertions)
.venv/Scripts/python.exe -m pytest tests/golden -v

# Skip slow tests (OCR integration tests that hit the real model)
.venv/Scripts/python.exe -m pytest -m "not slow"

# Lint
.venv/Scripts/ruff check .
```

OCR needs the optional extra: `pip install -e ".[ocr]"` (onnxruntime, opencv,
pyclipper, numpy). The two PP-OCRv6 model files (~31 MB) are committed to
`models/ocr/` as plain binaries — not git-lfs, which was configured on no
machine and left 132-byte pointer files in every clone — so nothing is
downloaded to run OCR. `pip install -e ".[ocr-hf]"` + `python -m
extractor.ocr.fetch_models` only re-downloads weights; the pipeline itself
never imports `huggingface-hub`.

Development runs on the system Python 3.14 (`python3`), which holds the core,
OCR and VLM extras; the `.venv` in the tree is stale and has none of them.

The VLM extra (`--vlm`, `extractor/vlm/`) is mlx-based and therefore
Apple-Silicon only. On every other platform `vlm/registry.py` reports
`UNSUPPORTED_PLATFORM` and the run is unchanged — so any behaviour the docs
attribute to the visual model does not happen on Windows or Linux.

## Architecture

**Dispatch by extension, one shared internal model.** `extractor/dispatcher.py`
maps each file extension to a reader function (`SUPPORTED_EXTENSIONS`). Every
reader builds its output exclusively through the constructors in
`extractor/model.py` (`make_text_block`, `make_table_block`, `make_unit`,
`make_document`, etc.) — that module is the single source of the
document/unit/block shape. Both writers (`json_writer`, `markdown_writer`)
consume that shape, so adding a new reader never touches the writers, and a
new output format never touches the readers. Both writers accept an optional
`stem` override; `main.py::unique_stems` computes a collision-free stem per
input before processing (`report.pdf` + `report.docx` -> `report-pdf` /
`report-docx`) so two inputs sharing a stem never overwrite each other's
output.

Document -> units (page / section / sheet / slide) -> blocks (`text`,
`table`, `header`, `footer`). `header`/`footer` blocks appear in JSON but are
excluded from Markdown. OCR text reuses the `text` block type on purpose (with
`source: "ocr"` and `confidence`), so it flows through Markdown, metrics, and
future chunking exactly like native text — see `make_ocr_text_block` in
`extractor/model.py`.

**PDF is the most involved reader.** `extractor/pdf_reader.py` combines
PyMuPDF (text) and pdfplumber (tables) and de-duplicates between them. Three
supporting modules feed it per-page signals and structure:
- `page_signals.py` — text chars, image area, fonts, ruling lines per page.
- `reading_order.py` — column-aware word reordering (clusters blocks into
  columns for multi-column layouts). A block spanning at least
  `FULL_WIDTH_FRACTION` of the text width is treated as a *banner* (title,
  rule, section heading) and kept out of the clustering: the greedy merge
  grows a column's span as blocks join it, so letting a full-width title in
  merges every column it crosses into one — a title over two columns then
  read right-before-left and emitted no two-column warning. Banners split the
  page into bands instead, each band clustered on its own, and the reported
  column count is the widest band's.
- `headers_footers.py` — detects repeated header/footer lines by signature
  across the document. `remove_lines_from_text` drops each header's *first*
  occurrence and each footer's *last*, not every match — a header that is a
  plain word (`SPECIFICATION`, a product name) would otherwise be stripped
  wherever it appears as a real body heading. Position alone cannot decide
  which is which, because the text arrives in column-aware reading order and a
  footer is often not the last line, so the caller passes the two bands
  separately.

Each page is classified (`page_class`: `native-text` / `scanned` / `mixed` /
`garbled` / `layout-complex`) from those signals, and the classification
decides whether/how OCR touches that page (see OCR below).

`garbled` is decided by replacement and non-printable ratios, which are blind to
the commonest form of PDF text damage: a **symbol font whose ToUnicode CMap maps
glyph IDs into arbitrary Unicode blocks**, so `τ_f = (σ − u)·tg φ'` extracts as
`௙ൌ ߪ െ ݑ · ݐ݃ ∅ᇱ`. Those are printable letters from real scripts, so every
ratio scores clean. `_unexpected_scripts` catches it on a different signal:
**how many distinct scripts a page mixes**. A document quoting a foreign phrase
uses one; a broken CMap scatters glyphs across NKO, Malayalam, Syriac and
Ethiopic at once, which no real document does. Two distinct unexpected scripts
is the threshold (`MISMAPPED_SCRIPT_COUNT`), counting letters and combining
marks only — including punctuation made it fire on ordinary pages.

It raises `MISMAPPED_GLYPHS` and **does not reclassify the page**. That is the
whole design: on the 388-page course it fires on 24 pages that are otherwise
sound prose — page 41 is 1,374 characters of which 7 are damaged. Marking them
`garbled` would route them down the replace path and bet 1,367 good characters
against a model reading, to recover 7 bad ones. The damage is real but it is
concentrated in formulas and symbols, so the honest output is intact text plus a
warning naming the scripts, not a silent substitution.

**pdfplumber and PyMuPDF do not share a coordinate space.** `extract_tables`
returns pdfplumber bboxes that the text pass tests against PyMuPDF word
coordinates. The two agree only on an upright page whose MediaBox starts at
`(0, 0)`; a rotated page is reported by pdfplumber in *rotated* space while
`get_text("words")` stays unrotated, and a shifted MediaBox offsets the two by
its origin. `pdf_reader._to_word_space` translates by the page's pdfplumber
origin and then applies `page.derotation_matrix`, which is why each table entry
carries an `origin` alongside its `bbox`. Without it the exclusion removes the
wrong words — table text duplicated into the text block, real prose dropped.

**OCR (`extractor/ocr/`) is a separate, optional layer glued in by
`apply.py`.** The PDF reader, `image_reader.py` and `pptx_reader.py` all call
into `apply.py::ocr_images` to get back a text block + warnings — that shared
glue means no reader talks to the OCR engine directly:
- `apply.py` — pools the lines from every image on a unit and averages
  confidence **over lines, not over images**, so a one-line thumbnail cannot
  outvote a fifty-line screenshot. A page whose average passes while
  individual lines fail gets `OCR_MIXED_CONFIDENCE`; the blended average alone
  reads reassuringly and hides exactly the lines worth checking. `[icon]`
  markers are excluded from the statistics but kept in the text — the marker is
  inserted here, not read by the model, so scoring it measures nothing and lets
  the icon fix degrade the page's reported quality. Sub-threshold fragments of
  at most `MAX_NOISE_FRAGMENT_CHARS` are dropped, because a string that short
  carries no redundancy to verify a low-confidence read against, while a
  garbled *sentence* keeps recoverable structure and is always kept and
  flagged. Length never discards on its own — the confidence gate decides — and
  every discard is reported as `OCR_NOISE_FILTERED` with a sample, so a
  document that legitimately holds short low-confidence text (form checkboxes,
  element symbols, single-letter cells) says so rather than losing it quietly.
  Each image is recognized in isolation: one image raising does not discard
  text already read from the others on the same unit. If every image fails,
  the unit gets `OCR_FAILED` (nothing was read, counted as unreadable in
  `main._UNREADABLE_CODES`); if only some fail, the unit gets `OCR_IMAGE_FAILED`
  alongside its recovered text (not in `_UNREADABLE_CODES`, since text exists).
  Recognized lines are reordered before they are pooled: `_in_reading_order`
  hands each line to `reading_order.reorder_words` as its own block, with pixel
  coordinates scaled to `NOMINAL_PAGE_WIDTH` so the point-tuned column
  thresholds serve both paths. The detector's own top-to-bottom sort interleaves
  a two-column scan line by line, which a chunker then splits and reglues. It is
  column clustering, not layout analysis, and the ceiling is measured: on a 2x2
  panel infographic (148 lines, 4 panels) the engine switched panel 57 times,
  this switches 34, perfect grouping would switch 3. `OCR_MULTI_COLUMN` fires
  where columns run concurrently, because that is exactly where the order is
  improved and still not guaranteed.
- `config.py` — run-wide `OcrConfig` (dpi, min-confidence, model dir), set
  once from the CLI and read by everyone else; readers take only a path, so
  options travel through this module instead of every function signature.
- `registry.py` — one-time cached availability probe (is a usable engine
  present?), with typed failure reasons.
- `onnx_engine.py` — PP-OCRv6 detection + recognition on onnxruntime (CPU).
  Every detected line is scaled to `REC_HEIGHT` (48 px) with its width
  following the aspect ratio, capped at `REC_MAX_WIDTH`. That cap is a memory
  bound, not a quality knob: set too low it compresses long lines of small
  text until glyphs merge and the spaces between words vanish, and the model
  then reports high confidence on the wreckage. Crops are batched with others
  of similar width (`_width_ordered_batches`), because a batch pads to its
  widest member — that is what keeps a generous cap cheap. `_read` restores
  reading order afterwards.
- `dbnet_post.py` — probability map -> quads (binarize, contours, pyclipper
  unclip, minAreaRect) + reading-order sort.
- `ctc.py` — greedy CTC decode against the vendored `ppocrv6_dict.txt`
  (18,708 entries); refuses to decode with `OCR_MODEL_INCOMPATIBLE` rather
  than emit garbage if the class count doesn't match the dictionary.
- `raster.py` — PDF page / figure / image file -> RGB array, capped
  by `DEFAULT_MAX_PIXELS` (reduces DPI instead of producing huge frames on big
  pages). `page_image_regions` is the entry point for a page's pictures, and
  the unit is the *figure*, not the stored image: authoring tools slice one
  diagram into strips (a 388-page course stored one as eight bands), and a
  strip alone means nothing to OCR. Placements within `IMAGE_REGION_GAP` that
  share `IMAGE_REGION_SPAN_FRACTION` of one dimension's span are merged;
  touching alone is not enough, or two figures that clip at a corner in a busy
  layout merge into a box that swallows the body text between them. Each
  region is then rendered *from the page* (`region_pixmap`) rather than pulled
  out by xref, which is what brings the callouts and legends along — they are
  page text drawn over the bitmap, absent from the stored image — and makes
  colorspace conversion moot, since the render is RGB by construction. The
  render DPI is the highest native density of the images the region covers, so
  nothing is upsampled past what the file holds. `MIN_IMAGE_PIXELS` drops icons and
  bullets *before* the grouping, so a decorative dot beside a figure cannot
  enlarge that figure's region; it measures the stored image, not its
  placement, because a detailed picture scaled down to a thumbnail still holds
  the pixels OCR needs.
  `pdf_reader._extract_images` consumes these regions to write the PNGs.
  `repeated_image_xrefs` identifies page furniture —
  an image whose *xref* is placed on most pages is a logo or a stamp, not
  content, and exporting it once per page is 100 copies of the same PNG.
  Identity is the xref, not the pixels: repeated content under *different*
  xrefs is usually bullets and icon fragments, which `MIN_IMAGE_PIXELS`
  already drops. Documents under `REPEATED_IMAGE_MIN_PAGES` are exempt,
  because "on most pages" says nothing about three pages. The set is resolved
  once per document in `pdf_reader.read_document`.
- `paths.py` — model file discovery/search order, `load_labels()`.

By page class: `scanned` and `garbled` pages are OCR'd by default, and always
as a whole rasterized page. On a `garbled` page the OCR text replaces the
damaged native text only above `--ocr-min-confidence` — below it the reading is
discarded and the page reports `OCR_REJECTED_LOW_CONFIDENCE` instead of
`OCR_APPLIED`, since claiming "text recovered by OCR" about text the document
does not hold is worse than saying nothing. Every other class keeps its native
text untouched.

`mixed` and `layout-complex` pages (`FIGURE_OCR_CLASSES`) have their pictures
read **only under `--ocr-figures`**, and only where the page has one. The
default is off because on a 388-page chart-and-map course that path produced
99k chars of axis labels and legend fragments — 68% of tokens ≤3 chars or
numeric, 26% duplicating the page text the figure was rendered over, since
`region_pixmap` renders the figure *from the page*. But the same path is the
only thing that reads an infographic deck: a 13-page reference deck lost 36% of
its characters without it, including a whole SAE L0-L5 table. Chart vs
infographic is a property of the document, not something a threshold can read
off one page, so it is a flag rather than a heuristic. Figures are exported as
images either way. OCR unavailability
(extra not installed, models missing, model/dictionary mismatch) never
crashes the run — it downgrades to a structured warning
(`OCR_UNAVAILABLE`/`OCR_MODEL_INCOMPATIBLE`/etc.) and the page still produces
valid output, just without that text.

**The visual layer (`extractor/vlm/`) is the second optional reader, glued in
by its own `apply.py` exactly as OCR is.** Same shape throughout: `config.py`
(run-wide settings), `registry.py` (one-time cached probe with typed reasons),
`engine.py` (one mlx-vlm wrapper for every supported model — thin on purpose),
`apply.py` (infer, cache, judge). Off unless `--vlm`; unavailable downgrades to
`VLM_UNAVAILABLE` and the run is unchanged.

**`--vlm-pages auto` (default) sends only the pages the reading can repair**:
`scanned`, `garbled`, and pages carrying `MISMAPPED_GLYPHS`
(`pdf_reader._vlm_candidates`). With no candidate the model is never loaded, so
`--vlm` on a healthy document costs nothing — the whole-document read that
`all` restores kept almost nothing from healthy pages at ~14 s each. The
describing pass asks `registry.host_failure()` (platform + deps, no weights)
rather than the reading probe, for the same reason: under `auto` there may be
no reading engine to consult, and loading granite to decide whether Qwen can
run would be the cost `auto` exists to avoid.

**One model, one table.** `--vlm-model` selects the prompt that makes a model
emit its format *and* the parser that reads it back — one choice, so it lives
in one place: `models.py`. granite-docling emits a `<doctag>` stream
(`doctag.py`) and produces a `ParsedPage`. PaddleOCR-VL had the second entry
until 2026-09-18 and was removed on measurement (README's visual-model section,
`docs/backlog-formulas-and-models.md`): built as an element recognizer behind a
layout detector, fed whole pages it ran past an 8192-token cap on half of them. An unrecognized name raises
`VlmUnavailable(UNKNOWN_MODEL)` rather than falling through to the wrong parser,
which fails invisibly: an empty page and `VLM_OUTPUT_REJECTED: empty` for every
page, with no hint why. granite is the default on measured evidence — see the
README's visual-model section.

**A formula is wrapped for display by `doctag.as_display_math`, not by the
caller.** Both models write multi-line equations as a bare *alignment body* —
`V_1 & = ... \\ & + ...`, the inside of an `align` environment — and neither
emits the environment around it. In bare `$$` that is a KaTeX parse error
(`Expected 'EOF', got '&'`) and the equation renders as nothing. The wrap lives
beside `formula_is_balanced` because both emission sites (`doctag.parse` and
`apply.py`'s redundant-page rebuild) need it and patching one leaves the other
broken. `formula_is_balanced` also counts braces, not
just `\left`/`\right`: a stray `}` inside a valid `array` renders as nothing
and the delimiter count cannot see it — 1 of 479 formulas on the reference
course, and that one was the bug.

**Truncation is proved by the token cap, not inferred from the text.**
`engine.convert` returns `(raw, capped)` from the model's own
`finish_reason == "length"`. Prose cut mid-sentence looks exactly like prose
that ended there, so any parser defers to the cap first; `doctag.is_truncated`
adds its tag-balance check on top. A rejected page keeps its native text.

That cap is now the backstop, not the primary defence. `--vlm-repetition-penalty`
(default 1.05) stops the loops at the sampler instead of paying for them and
discarding the result: a garbled page that repeated one fragment 51 times ran to
the full 4096-token cap in 39 s unpenalized, and stopped on its own after 1382
tokens in 12 s with usable content once penalized. 1.05, 1.1 and 1.2 all fixed it
identically, so the default is the gentlest — the penalty falls on legitimately
repeated tokens too, and a table's repeated headers and numeric cells are exactly
that, which is why 1.0 disables it. The penalty is part of the inference cache
key: without it a page cached as a loop would be served back forever after the
setting that fixes it was turned on. Lowering `--vlm-max-tokens` was considered
and rejected — healthy pages cost 1475 tokens on average against the 4096 cap, so
the cap never binds on the normal path and only unused budget would be cut.

**That average is granite's, and it did not carry to the other model.** On the
388-page geotechnics course PaddleOCR-VL (removed 2026-09-18) hit the cap on
**143 of the 245 pages it attempted, against granite's 12** — 58% against 5% —
and doubling its cap to 8192 changed nothing: 28 of 56 damaged pages still
capped, ten of them literal repetition loops. Markdown spends far more tokens
than a doctag stream on a dense page, but the cause is deeper: the model is an
element recognizer built to run behind a layout detector, and whole pages are
off-label for it. On the pages it finished it transcribed more cleanly and
never split Romanian words around diacritics; that advantage is now
`doctag.join_split_diacritics` on granite's output (README, visual-model
section). The experiment is in `docs/backlog-formulas-and-models.md`.

The truncation *cascades*, which is the non-obvious part. A rejected page never
sets the `skip=` that tells `_ocr_pages` the visual model already handled it, so
its garbled prose falls through to OCR: 24 OCR'd pages on the PaddleOCR-VL run
against 2 on granite's, from identical input and an identical 35-page
`GARBLED_TEXT` count. Read an OCR-page count difference between two visual
models as a truncation symptom before reading it as an OCR one.

The whole document is read, and that is the expensive half of a decision the
cheap half of which is *keeping almost none of it*. On a healthy page the model
returns the native text back 92-100% word for word (measured across 22 pages of
two reference PDFs), so publishing it beside the text it copies doubles the
document for nothing — the embedded-figure OCR mistake in a new form. So
`apply.py` keeps an additive page's prose only above `MIN_PROSE_NOVELTY`, and
otherwise keeps just its formulas and its tables; a page left with neither is
rejected as `duplicate` — counted once for the whole document
(`VLM_OUTPUT_REJECTED` with a `pages` count) rather than listed per page, since
on a text-heavy document this is the common case and a line each would bury
the pages where something actually went wrong. `scanned`/`garbled` pages are
the exception — there the reading *replaces* the untrusted native text,
`_ocr_pages` skips the page (`skip=`), and two recovery paths never print the
same page twice. That skip only fires when the model's reading actually
carries text: a page kept for its tables alone replaces nothing, so OCR still
gets a shot at the garbled prose standing beside them. Native tables always win
over the model's: pdfplumber reads ruling lines, the model infers them.

**The reading model fabricates URLs, and nothing in the pipeline catches it.**
Measured on the 388-page course: of the URLs appearing in the model's kept text,
granite invented 11 of 13 and PaddleOCR-VL 8 of 9. They are not garbled — they
are plausible, well-formed and wrong: `jrbengineering.com` came back as
`thiborgineering.com`, `pilingindustrycanada.com` as `sgcengincovering.com`.
These shipped into the Markdown. It is the `formula_is_balanced` problem in a
form no balance check sees — a wrong equation that renders is worse than a
missing one, and a wrong URL is worse still, because it looks like a citation
and Phase 2 will embed it as one. That it appears in both models at the same
rate makes it a property of the reading path, not a reason to prefer one model.
No gate exists for this yet; a page carrying model-read URLs is worth
distrusting by hand until one does.

`MIN_YIELD_RATIO` guards only the replacing case. Applied to an additive page
it would reject a reading that returned one clean formula plus a paragraph,
which is exactly the contribution worth having. Formulas failing
`formula_is_balanced` are dropped rather than emitted, because a wrong equation
that renders is worse than a missing one — nothing flags it. `is_truncated`
excludes `<other>`: the model emits it bare inside `<picture>`, and counting it
as a paired tag reported every illustrated page as truncated.

**Figure description (`vlm/describe.py`) is a third reader, not a third
format.** `--vlm-describe-figures` loads a second model beside the reading one
and asks a different question: not what the page says, but what its charts,
maps and schematics *show*. The answer is prose, so there is no parser and no
entry in `models.py` — that table maps a model to a page-conversion prompt and
the parser for its format, and `--vlm-model` picks one model for the whole
reading path. Description is additive and runs beside granite, so a second
dispatch axis inside that table would buy nothing.

It reads whole pages, not figure crops. Cropping was the obvious design and the
corpus refuted it: `raster.page_image_regions` returns zero regions on 16 of 17
pages of the reference deck (vector art, no embedded raster), and granite's own
`<picture>` boxes there are 12x11-unit logos, absent entirely on two pages that
do have figures. Both region sources fail on the half of the corpus that
motivates the feature.

The page set is `FIGURE_CLASSES` **or** a non-zero image count, not the class
alone: the course's seismic-zoning page — a full-page contoured map of Romania,
the most describable page measured — classifies `native-text`, so gating on
class alone silently skipped it. The image count is the signal `_ocr_pages`
already uses for its figure pass.

`describe_image` is the same pass for a standalone image file, called from
`image_reader`. It is additive only and has no reading model beside it: an
image's text comes from OCR, which is *trusted*, unlike a `scanned` page's
native text that the model may replace — so pairing a conversion model with it
would bet verified text against an unmeasured reading and would duplicate
`pdf_reader`'s merge rule in a second place. It also skips `registry`
deliberately: that probe is for the reading model, and consulting it here would
load granite's weights only to decide whether a different model can run.

A figureless page is refused by the model (`NONE`), not by an output heuristic —
there is no region signal to threshold. That is a prompt instruction, so it was
measured rather than trusted: 26 pages sampled across the course gave 8 bare
sentinels, 18 descriptions (median 1,420 chars), 0 refusals phrased as prose,
and 0 hitting the 512-token cap. The prose form is the one that matters — it
would publish as a figure block reading "there are no figures on this page" —
so re-measure it before changing the prompt or the model.

Cost is ~45 s per page sent and a second set of resident weights (7.4 GB peak
for granite + the 4-bit Qwen3-VL), which is why it is a flag and off by default.
The page count is the number that matters and it is large: the widened gate
sends 330 of the course's 388 pages (the class alone would send 181, and would
miss the seismic map). Roughly four hours for that document — quote the page
count, not the per-page seconds, when anyone asks what it costs.
`apply._infer`'s cache keys on the model, so the describing pass never collides
with the reading pass over identical pixels.

**Warnings are structured and travel everywhere.** Every recoverable quality issue the
pipeline detects (garbled text, scanned page, encoding fallback, OCR outcome,
...) becomes a `{"code": ..., ...}` dict on `document.warnings`. Fatal failures
are different: they use `extractor/errors.py` and a stable category (`unsupported`,
`malformed`, `encrypted`, `resource_limit`, `missing_part`, or `io`) and are not
attached to a nonexistent document. Warnings render as an `## Extraction Notes`
section in Markdown and in the console run summary — one source of truth, three
renderings. See the README's "Warning Codes" table for the full list before
adding a new one.

**Strong-signature validation.** `extractor/format_detection.py` validates PDF,
RTF, and OOXML signatures before the extension-selected reader runs. It checks
OOXML `[Content_Types].xml` and the expected main-document type, while CSV, TXT,
Markdown, HTML, JSON, and XML retain extension-based fallback because their
content signatures are ambiguous. Mismatched or incomplete strong packages fail
with a typed category; unsupported extensions retain the existing unsupported
behavior.

**Failure isolation.** `main.py` iterates `input/` filtered by
`SUPPORTED_EXTENSIONS`, and one file's failure (oversized, corrupt, unsupported
internal structure) is caught, logged, and counted in the summary without
stopping the batch. `main()` returns a process exit code so that isolation
stays visible to callers — 0 clean (an empty input directory included), 1 when
any file failed, 2 when the input directory does not exist. A batch where
everything failed must not look like a clean run to a scheduler or the
launcher.

There is no page cap. `--max-pages` was documented and parsed but never
enforced, and was removed rather than implemented — see finding 10 in
`docs/code-audit-2026-08-31.md`. The `--max-file-mb` size guard is the input
file limit, and structured readers also enforce expansion budgets before
materialization. The image/OCR default is 25,000,000 pixels per raster; larger
images fail visibly as `resource_limit` for now. A future resize policy must be
explicitly tested for OCR quality before replacing refusal with downscaling.
JSON and XML refuse depth/container or node breaches, and tables refuse more
than `MAX_TABLE_CELLS`; none of these limits silently truncate content.

The file-size guard measures the *compressed* size: XLSX compresses roughly
10:1, so an 80 MB workbook passes the 100 MB cap and can still expand to
millions of cells in memory.

`MAX_WORKBOOK_ROWS` / `MAX_WORKBOOK_CELLS` in `limits.py` bound that expansion.
They **refuse** the workbook (`WorkbookTooLargeError`) rather than truncating
it — a partial workbook is silently wrong, while a refusal is one failed file
the batch reports and steps over. That distinction is the whole point: an OOM
would take every other file down with it, and this is the one place the
pipeline's per-file failure isolation would otherwise not hold. It is *not* the
`--max-pages` trade-off, which silently dropped content from a document that
still reported success.

Both caps are needed: rows alone miss a sheet 40 rows by 5,000 columns, cells
alone accept a million-row sheet one column wide. `_preflight` reads each
sheet's dimension record (available even in read-only mode) so an oversized
workbook is refused before a single row is materialized; the running count in
`_sheet_rows` is the backstop for a sheet with no dimension record, or one that
understates it.

## Adding a new format

1. Write the reader in `extractor/`, building output only through
   `extractor/model.py` constructors.
2. Register its extension(s) in `SUPPORTED_EXTENSIONS` in
   `extractor/dispatcher.py` (multiple extensions can map to the same reader —
   see `.xlsx`/`.xlsm`).
3. Update the `SUPPORTED_EXTENSIONS` set assertion in
   `tests/test_dispatcher.py` and add a routing test.
4. Update the "Supported Formats" table and the architecture diagram in
   `README.md`.

## Testing notes

- `tests/conftest.py` generates fixture files at runtime (no binary fixtures
  committed for the common case).
- `tests/golden/` holds a curated edge-case corpus with semantic metric
  threshold assertions (`tests/metrics.py`), not exact-output assertions.
- `tests/baseline/` guards against regressing v1.1 (pre-OCR) output —
  `generate.py` rebuilds `snapshot.json` from the `v1.1-pre-ocr` tag if it
  ever needs to change intentionally.
- `slow` and `integration` markers are registered in `pyproject.toml`;
  `test_ocr_integration.py` exercises the real ONNX models and is marked
  `slow` (auto-skipped by `-m "not slow"`).
- `test_ocr_apply.py` and `test_ocr_engine.py` cover the OCR glue and the
  recognition input preparation with a stub engine / stub ONNX session, so
  they need no model weights. Prefer that pattern over adding to the `slow`
  integration file when the behaviour under test is not the model itself.
- `test_vlm_registry.py` / `test_ocr_registry.py` test the availability probes
  directly — dependency check, platform check, unknown-model check, probe
  caching, `reset()` — rather than monkeypatching `is_available()` past them.
  Both probes have caused real production incidents (a missing dependency
  going unreported, an order-of-operations bug hiding the unavailability
  warning); a test that stubs the boolean instead of the logic behind it
  cannot catch a regression in that logic. Apply the same pattern to any new
  probe/registry module.

## Debugging OCR quality

Before building a heuristic to repair OCR output, prove the signal it depends
on actually exists — write a throwaway probe against a real document and
measure it. Two proposed fixes for dropped spaces were killed this way in
minutes: the CTC blank run beside a real space is no longer than one inside a
word (so gap-splitting would be ~81% false positives), and where a space goes
missing the model gives that class ~0.0000 probability rather than narrowly
losing it. Both results pointed upstream, to preprocessing throwing pixels
away before the model ever saw them — which was the real bug. Suspect our own
image handling before concluding "model limitation".

## Out of scope (Phase 1)

Legacy binary `.doc`/`.xls`/`.ppt` are not supported. OCR targets printed
text, not handwriting. Embeddings, vector DB, semantic search, and LLM
integration are Phase 2+.

Now the real verification — Run full pipeline on all three documents.
