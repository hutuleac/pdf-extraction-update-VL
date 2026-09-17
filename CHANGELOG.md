# Changelog — Knowledge Extraction Pipeline

## Unreleased — test coverage for the two incident-causing modules

### Tests

- **`extractor/vlm/registry.py` and `extractor/ocr/registry.py`** had no
  dedicated tests despite being the exact code behind two prior production
  incidents (a missing `torchvision` dependency going unreported, and an
  order-of-operations bug that hid the `VLM_UNAVAILABLE` warning). Every
  existing test stubbed `registry.is_available()` directly, so a regression in
  the platform check, the dependency check, or the probe's failure-caching
  would never fail a test. Added `tests/test_vlm_registry.py` and
  `tests/test_ocr_registry.py` (17 tests): unsupported-platform-before-
  missing-deps ordering, missing-deps naming, unknown-model fast-fail,
  unexpected-exception downgrade, probe-once caching, `reset()`, `get_engine()`
  success/failure. Coverage: `vlm/registry.py` 38.9% -> 92.6%, `ocr/registry.py`
  90.2% -> 100%.
- **`extractor/vlm/engine.py`** (`MlxVlmEngine`, `render_page`) had 0%
  dedicated coverage. `convert()`'s `capped` return value is the only
  truncation evidence the pipeline has — Markdown gives no other signal, since
  cut-off prose looks identical to prose that ended there. Added
  `tests/test_vlm_engine.py` (10 tests, `mlx_vlm.load`/`generate` stubbed):
  text extraction from both the `.text`-attribute and plain-string result
  shapes, truncation via `finish_reason`, truncation via the token-count
  fallback when `finish_reason` is absent, non-truncation, and that
  `max_tokens`/`repetition_penalty` are actually forwarded. Coverage: 39.3% ->
  100%.
- **`extractor/vlm/describe.py`** (Qwen3-VL figure descriptions) was tested
  only on its happy paths; `_get_engine`'s own probe/cache/failure logic, and
  `describe_pages`/`describe_image`'s unavailable-engine and truncation-
  reporting branches, were untested — the same blind spot as the registries
  above, for the describing model instead of the reading one. Coverage: 76.4%
  -> 100%.
- **`extractor/pdf_reader.py`'s `_ocr_pages` dispatch loop** (rasterize ->
  recognize, full-page vs. figure-page branching, per-page rasterization-
  failure isolation) was never reached by `tests/test_ocr_pipeline.py`, whose
  only fixture forces OCR unavailable. Added a `with_ocr` fixture (fake
  engine, real dispatch) and 6 tests. Coverage: 82.5% -> 89.3%.
- **`extractor/warning_text.py`** (every warning code's human-readable
  rendering, read by both the Markdown notes sidecar and the CLI summary) had
  no dedicated test file. Added `tests/test_warning_text.py` (23 tests)
  covering every embellishment branch and the module's "never raise on a
  malformed warning dict" guarantee. Coverage: 75.0% -> 100%.
- Full suite: 535 tests passing (`-m "not slow"`), overall coverage 89.4% ->
  93.0%.

### Fixed

- Two outstanding `ruff check` findings: a nested `if` in
  `extractor/rotated_text.py` collapsed into one condition (SIM102), and a
  single-element list slice in `tests/test_vlm_apply.py` replaced with `next()`
  (RUF015).

## Unreleased — rendering fixes and the 388-page model comparison

### Fixed

- **Multi-line equations now render.** Both visual models emit a bare
  *alignment body* (`V_1 & = ... \\ & + ...`) with no environment around it;
  wrapped in bare `$$` that is a KaTeX parse error and the equation displays as
  nothing. `doctag.as_display_math` adds `\begin{aligned}` when a body needs
  one, and all three emission sites (`doctag.parse`, `markdown_doc.parse`,
  `apply.py`'s redundant-page rebuild) route through it. 58 formulas across 43
  pages of the reference course were affected.
- **`formula_is_balanced` now counts braces**, not only `\left`/`\right`. A
  stray `}` inside an otherwise valid `array` renders as nothing and the
  delimiter count cannot see it. 1 of 479 formulas on the course fails the new
  check, and that one was the bug.

### Added

- **`MISMAPPED_GLYPHS`** warning for pages whose symbol font decoded through a
  broken ToUnicode CMap — `τ_f = (σ − u)·tg φ'` extracted as
  `௙ൌ ߪ െ ݑ · ݐ݃ ∅ᇱ`. Those are printable letters from real scripts, so every
  existing garble ratio scores them clean. The discriminator is how many
  *distinct* scripts a page mixes: a quotation uses one, a broken CMap scatters
  glyphs across NKO, Malayalam and Syriac at once. Fires on 21 of 388 pages;
  314 of 334 text pages score zero, so the signal is a split, not a gradient.

  It deliberately **does not reclassify** the page. Page 41 is 1,374 sound
  characters of which 7 are damaged; calling it `garbled` would route it down
  the replace path and bet the 1,367 against a model reading.

### Measured

- **Full 388-page course run through both reading models.** granite kept 170
  pages against PaddleOCR-VL's 102, and the whole gap is the token cap:
  PaddleOCR-VL truncated on 143 of 245 attempted pages against granite's 12,
  because Markdown costs more tokens than a doctag stream on a dense page. The
  truncation cascades — a rejected page never sets `skip=`, so its garbled prose
  falls through to OCR (24 pages against 2).
- **The formula count measured notation, not recovery** — now fixed.
  `markdown_doc._FORMULA` matched display delimiters only, so PaddleOCR-VL's
  486 inline `\(...\)` formulas were never counted and never validated. With
  inline maths counted, the model reports **827 formulas against granite's
  478**, reversing that half of the comparison, and 29 pages previously
  discarded as duplicates are kept because their equations now register.
  Inline formulas are counted in place rather than promoted to display: 338 of
  the 486 sit inside a sentence, and lifting one out would cut it in half. An
  unbalanced inline formula is counted as rejected but left visible, where a
  display one is deleted — removing it would leave a hole in the sentence.
- **Both models fabricate URLs** — granite 11 of 13, PaddleOCR-VL 8 of 9 — in
  well-formed, plausible, wrong form (`jrbengineering.com` →
  `thiborgineering.com`). These shipped into the Markdown. No gate exists.
- **A page diff refined the model choice.** The two read largely *different*
  pages (50 of 221 shared). On the shared ones PaddleOCR-VL is the more accurate
  transcriber: granite scrambled one equation and dropped a factor from another
  while staying balanced, and splits Romanian words around diacritics 848 times
  against PaddleOCR-VL's zero. granite stays the default on coverage.
- **Pairing a conversion model with OCR on standalone images was measured and
  rejected**: zero tables emitted on the reference card, 72 lines read against
  OCR's 146, 1% novel vocabulary.

## Unreleased — extraction safety and diagnostics

### Added

- Added stable fatal categories: `unsupported`, `malformed`, `encrypted`,
  `resource_limit`, `missing_part`, and `io`.
- Added bounded signature validation for PDF, RTF, and OOXML packages before
  extension-selected dispatch.
- Added JSON/XML depth and container/node budgets, image raster limits, and a
  shared table-cell budget. Limit breaches refuse the file instead of returning
  partial content.
- Raised the default OCR raster budget to 25 MP. Inputs above that budget still
  fail visibly; adaptive resizing remains a planned follow-up.
- Added malformed-package, mutation, OCR-budget, table-budget, and batch
  isolation regression tests.
- Added extraction-quality evaluation notes and review priorities to
  `backlog.md`.

### Documentation

- Updated `README.md` and `CLAUDE.md` with fatal diagnostics, signature
  validation, resource budgets, and the current OCR pixel policy.

## v2.0.0 — Fork merge: figure export, false-table fix, visual model

Merges the evolved fork's Phases 1.3-1.6 onto this history as real commits
(not a directory swap): embedded figure export with watermark and
rasterization fixes, the false-table-detection fix that recovers whole slide
bodies from single-cell tables, an optional `--vlm` layer (granite-docling,
Apple Silicon only), and `--ocr-figures` restoring figure OCR as an opt-in
after 1.5 removed it by default. Full detail in the phase entries below.

Verified on the golden corpus, the pre-OCR baseline snapshot, and three real
reference PDFs before merge; 410+ tests pass (`-m "not slow"`).

## Phase 1.6 — Figure OCR returns as an opt-in

### Added

- **`--ocr-figures` restores the pre-1.5 path** for pages classed `mixed` or
  `layout-complex` that hold a picture. Off by default, so the clean 1.5 output
  is unchanged.

  Phase 1.5 cut that path on the evidence of one 388-page chart-and-map course,
  where it returned axis labels and duplicated page text, and handed the job to
  the VLM. The VLM is Apple-Silicon only, so on any other machine the removal is
  simply a loss — and it is not always a loss of noise. Measured on three
  reference PDFs run end to end:

  | Document | 1.4 chars | 1.5 chars | real content lost |
  |---|---|---|---|
  | CS.00244 (105p spec) | 299,240 | 292,446 | none — junk OCR, empty grids, duplicated tables |
  | GSN_STANDARD (71p) | 130,369 | 129,945 | none — prose freed from false table cells |
  | OPTEVA (13p deck) | 16,198 | **10,442** | **~264 words** — a whole SAE L0-L5 infographic and the institute name |

  The split is the document type, not the code: where a figure is a chart, its
  text is labels; where a figure is an infographic, its text is the page. One
  threshold cannot tell those apart from inside a page, so this is a flag.

## Phase 1.5 — The visual model replaces figure OCR

### Removed

- **Embedded figures on `mixed` and `layout-complex` pages are no longer
  OCR'd.** Measured on the 388-page reference course, that path produced 165
  OCR blocks totalling 99k characters — 15% of the document's text — of which
  68% of tokens were three characters or shorter or numeric (axis labels,
  legend fragments, map city names) and 26% duplicated the page's own native
  text. The duplication was structural: `region_pixmap` renders a figure *from
  the page* so callouts and legends come along, which also re-reads the slide
  title drawn over it.

  130 of those blocks sat on `layout-complex` pages and 35 on `garbled` ones.
  Only the garbled path recovers anything — it replaces text the document
  itself holds wrong — so only the figure path was cut. Region grouping stays:
  it feeds the image export, which is the point of a figure. The now-unreachable
  `REPEATED_IMAGE_SKIPPED` warning went with it. `ocr.raster.page_images_to_arrays`
  has no production caller left; it stays only because `tests/test_ocr_raster.py`
  uses it to exercise `region_pixmap`'s rendering, which the export path still
  depends on.

  After: 35 OCR blocks (all on garbled pages), 57k OCR characters,
  `OCR_NOISE_FILTERED` 108 -> 25, Markdown 860k -> 798k characters, all 617
  figures still exported.

### Added

- **`--vlm`: granite-docling-258M reads every page, locally, on Apple
  Silicon.** A new `extractor/vlm/` layer mirroring `extractor/ocr/` —
  module-level config, a cached availability probe with typed reasons, a thin
  mlx engine, and an `apply.py` that is the only module readers import. Four
  new warning codes (`VLM_APPLIED`, `VLM_OUTPUT_REJECTED`, `VLM_UNAVAILABLE`,
  `FORMULA_REVIEW_REQUIRED`). Off by default; unavailable never crashes a run.

- **Output is filtered by what the page does not already hold.** The first real
  run showed the model returning native text back 92-100% word for word on
  healthy pages — the figure-OCR mistake in a new form. An additive page now
  keeps its prose only when at least 30% of the words are new, otherwise just
  its formulas and its tables, and a page left with neither is rejected as a
  duplicate. On two reference PDFs that took 22 accepted pages down to 1: the
  one page carrying something new, a two-column table pdfplumber had missed.
  A `scanned`/`garbled` page is the exception — the reading replaces the
  untrusted text and OCR skips the page, so one page never gets two competing
  recovery blocks. That skip only fires when the model's reading carries text:
  a page kept for its tables alone replaces nothing, so OCR still gets a shot
  at the garbled prose beside them. Duplicate rejections are counted once per
  document (`VLM_OUTPUT_REJECTED` with a `pages` total) rather than listed per
  page, since on a text-heavy document this is the common case.

- **Per-page inference cache** under `~/.cache/knowledge-extractor/vlm`, keyed
  on the rendered page, model and token budget. A 400-page run is most of an
  hour; an interruption must not cost all of it.

## Phase 1.4 — Prose no longer swallowed by false table detection

### Fixed

- **A slide's body text was extracted as a one-cell table instead of prose.**
  `table_reader._is_real_table` accepted any detection with at least 2 rows and
  2 columns. A grey title bar over a content box — the layout of a lecture
  slide — reads to pdfplumber's line detection as a 2x2 grid, so whole slide
  bodies landed in a single Markdown cell joined by `<br>`. It also cost the
  text twice over: because `pdf_reader` excludes accepted tables' bboxes from
  the PyMuPDF text pass, that prose never reached reading order, header/footer
  handling, or the word-level rotated-text filter either.

  `_is_real_table` now also requires the median non-empty cell to be under
  `_MAX_MEDIAN_CELL_CHARS` (120), and rejects an all-blank grid. Shape alone
  cannot separate the two cases; cell length can, and by two orders of
  magnitude — on the reference document a real table's median non-empty cell is
  2-14 characters and a prose box's is 200-1900.

  Generalizes to any PDF whose layout boxes are drawn with ruling lines
  (slide decks, bordered callouts, sidebars). It does not depend on this
  document's fonts, language, or watermark. ponytail: the threshold is
  calibrated on one slide-deck PDF; a spec table with a long free-text Notes
  column could trip it — widen by judging the shortest column rather than all
  cells if that shows up.

  Measured on the 388-page reference document (native text only; OCR
  unaffected by this change):

  | | before | after |
  |---|---|---|
  | native text extracted | 342,430 chars | **614,741 chars** (+80%) |
  | table blocks | 282 | 137 |
  | characters held in tables | 288,987 | 61,677 |
  | prose boxes (median cell >=120) | 124 | **0** |
  | cells with reversed rotated text | 11 | 1 |
  | pages losing >100 chars | — | **0** |

  145 detections dropped: the 124 prose boxes plus 21 entirely blank grids.
  156 pages gained more than 100 characters of prose; none lost any.

- **Reversed rotated text stopped leaking into most cells, as a side effect.**
  90 deg-rotated photo-credit URLs (`.gnireenignenzj.www//:ptth`) appeared
  mid-paragraph on 10 pages. They were a symptom of the bug above, not a
  separate defect: once a prose box is rejected, its text is read by the
  PyMuPDF pass, whose word-level rotated-text filter drops them. One case
  remains, inside a genuine table on page 218, where the char-level path still
  applies.

### Notes

- `docs/spike-2026-09-05-granite-docling.md` records the granite-docling-258M
  evaluation that turned this bug up, and what it means for the planned
  visual-semantic fallback.

## Phase 1.3 — Image extraction, watermark and rasterization fixes

Found and fixed while processing a 388-page scanned/native-mixed textbook
(watermarked, formula- and diagram-heavy) end to end and inspecting the
output for quality. All four changes are general pipeline fixes, not
document-specific hacks — see the "Fixed"/"Features" notes below for exactly
what does and doesn't generalize to other PDFs.

### Features

- **Embedded images are now extracted, not just counted.** `extract_document`
  and `extract_pdf` take an optional `images_dir`; when given, each page's
  embedded raster images are saved there (skipping page-furniture logos via
  the existing `repeated_image_xrefs` and icons under 50px) and referenced
  from the Markdown as `![](...)`. `main.py` always passes
  `<stem>_images/` next to the `.md` file. Without `images_dir` (e.g. in
  existing tests), behavior is unchanged — images are still only counted.
  New `make_image_block` in `model.py`. On the reference document: 901 images
  recovered that were previously silently dropped, including diagrams and
  schematics on pages that had no other extractable content.
  ponytail: images are appended after a page's text/table blocks rather than
  interleaved at their true position — upgrade to position-based ordering
  (`reading_order.py`) if reading order across a figure starts to matter.

### Fixed

- **A vertical (90/270 deg) margin stamp leaked into table cells as reversed
  text.** `rotated_text.py`'s existing angle-based filter only drops
  *skewed* (diagonal) stamps — axis-rotated text is deliberately kept as
  legitimate content (sideways table headers, chart axis labels). This
  document's stamp was axis-rotated, not skewed, so it survived and showed up
  as garbled reversed tokens ("roliruzulat" ⇐ "taluzurilor") mixed into 256
  table cells across the book. Angle alone can't distinguish a vertical stamp
  from a real vertical label — only repetition can, so `repeated_vertical_boxes`
  adds that one check: vertical text repeating on ≥5 pages (document-wide, not
  a per-page heuristic) is treated as a stamp and dropped from
  `table_reader.py`'s char filter; a one-off vertical label (real content)
  is untouched. Generalizes to any document with an axis-rotated repeating
  stamp; a no-watermark PDF or a purely diagonal one is unaffected (the
  vertical scan finds nothing to drop). Does not catch a repeating stamp that
  is neither skewed nor axis-rotated (e.g. mirrored-but-horizontal) — not
  observed in the wild yet, so not built.
- **`page_images_to_arrays` crashed a page's OCR when it held an image mask.**
  An image mask / stencil (PyMuPDF `Pixmap.colorspace is None`, `n=1`,
  `alpha=1`) has no color data of its own; the code tried to RGB-convert it
  anyway, and PyMuPDF raised `ValueError: source colorspace must not be
  None`, which took the whole page down as `OCR_FAILED`. Masks are now
  skipped like any other unreadable image instead of attempted. This is a
  general bug fix — any PDF with an embedded image mask on a `mixed` or
  `layout-complex` page would have hit it; fixed 7 previously-unreadable
  pages in the reference document, 0 regressions in the other 381.
  (Chased a red herring first: a process-wide PyMuPDF state theory with a
  subprocess-retry workaround, which was unnecessary once the real cause —
  this one `if` check — was found. Left out of the final diff.)
- **Table cells glued adjacent words together** ("coeficientulluiHazen"
  instead of "coeficientul lui Hazen"). pdfplumber's default `x_tolerance`
  (3pt) treated this book's tightly-kerned ~2.5–2.7pt word gaps as
  intra-word spacing; lowered to 1.5pt for table-cell text extraction only
  (`table_reader._CELL_TEXT_X_TOLERANCE`), which sits well below the
  measured real word gaps and well above intra-word kerning (~0pt). This is
  a global default for all documents' tables, not conditional on detecting
  tight kerning — safe for normal spacing, and the only realistic risk is a
  document with legitimate ~1.5–3pt inter-character gaps (rare) getting an
  unwanted space inserted.

### Tests

- 6 new tests: `is_vertical` classification, `repeated_vertical_boxes`
  (stamp detected across 6 pages / one-off label kept / short document
  exempted), end-to-end vertical-stamp-in-a-table-cell (dropped when
  repeated, kept when it's a one-off), image-mask skip in
  `page_images_to_arrays`, `make_image_block`, and image-block Markdown
  rendering. 354 passing (was 348).

## Phase 1.2 — OCR

Scanned pages and image files are now read instead of silently skipped. Fully
offline: the PP-OCRv6 weights ship in `models/ocr/` via git-lfs and nothing is
downloaded at run time.

### Features

- **OCR engine** — PP-OCRv6_small detection + recognition on onnxruntime (CPU),
  in `extractor/ocr/`: DB post-processing with `pyclipper` unclip, perspective
  crop, batched CTC decode. ~0.3 s per page.
- **Vendored character dictionary** — `ppocrv6_dict.txt` (18,708 entries,
  Apache-2.0). Decoding is refused with `OCR_MODEL_INCOMPATIBLE` if the class
  count does not match, rather than emitting plausible-looking garbage.
- **PDF integration** — `scanned` and `garbled` pages are rasterized whole;
  `garbled` native text is replaced only when OCR is confident enough; `mixed`
  and `layout-complex` pages have only their embedded images read (when any
  exist), so nothing is duplicated; `native-text` is never OCR'd.
- **Image files as a format** — `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`,
  `.webp`, one section unit each.
- **OCR provenance** — OCR text is a normal `text` block carrying
  `source: "ocr"` and `confidence`, so it reaches Markdown, metrics and phase 2
  automatically. Non-OCR blocks are byte-identical to before.
- **Graceful degradation** — with no model or no OCR extra, every affected page
  reports the reason and the fix in the JSON, in a new `## Extraction Notes`
  Markdown section, and in the console summary. Never a crash, never a silently
  empty page.
- **New flags** — `--no-ocr`, `--ocr-model-dir`, `--ocr-dpi`,
  `--ocr-min-confidence`. `file_stats()` gains `ocr_pages` and
  `unreadable_pages`.
- **Pixel budget** — pages are rasterized at up to ~8 MP, degrading DPI on
  oversized pages instead of allocating 27 MB frames.
- **Pre-OCR baseline snapshot** — `tests/baseline/snapshot.json`, generated from
  the `v1.1-pre-ocr` tag and asserted on every run, proves OCR changed nothing
  for documents that never needed it.
- **45 new tests** (185 total). CTC, DB post-processing and the whole
  degradation path run with no model present; the real-model round trip is
  marked `slow`/`integration` and skips itself when the weights are absent.

### Fixed

- **`[icon]` markers no longer drag down the page confidence.** The marker is
  inserted by the pipeline, not read by the model, so its score measured
  nothing about the text — yet it was averaged into the page confidence and
  counted as a weak line. The icon fix was actively degrading the reported
  quality of every page it touched. On the 77-page reference deck this alone
  accounted for 25 of the 87 lines flagged as weak; 35 of the 60 markers
  scored *above* threshold, so removing them corrects the average in both
  directions rather than simply raising it.
- **Unreadable 1–3 character fragments are discarded instead of shipped as
  text.** A sub-threshold read that short has no internal redundancy, so it
  cannot be verified or recovered from context — unlike a garbled sentence,
  which keeps enough structure to be worth surfacing and is still kept and
  flagged. Length alone never discards anything: the confidence gate decides,
  and the length bound only limits how much it may throw away, so confident
  short content (`OK`, `10`, `X`) is untouched. Whatever is discarded is
  reported as `OCR_NOISE_FILTERED` with a quoted sample, so a document that
  legitimately holds short low-confidence text says so instead of going quiet.
  On the reference deck: 62 fragments / 72 characters removed (0.19% of OCR
  output), no word of three or more letters lost, and `OCR_MIXED_CONFIDENCE`
  went from 24 pages to none — the residual weak lines were entirely noise.
- **De-hyphenation no longer eats dashes that are not word wraps.** The rule
  matched any hyphen followed by whitespace, without checking what preceded it,
  so it joined far more than words broken across a line: numeric ranges lost
  their dash and fused into invented values (`10 - 1000` became `10 1000`,
  `5- 300` became the number `5300`), leading dashes on list and log lines were
  swallowed into the previous line, and a spaced dash collapsed two words
  (`well - known` became `wellknown`). It now requires a letter on both sides
  and the hyphen tight against the first half, which is what a wrapped word
  actually looks like. On the reference deck this restored 178 characters and
  7 line breaks with no word gained or lost and no wrapped word left unjoined.
  Note the pre-OCR baseline snapshot does not cover this rule — it contains no
  text where either the old or the new pattern fires — so the unit tests in
  `tests/test_normalizer.py` are what guard it.
- Console output no longer mangles dashes and diacritics on Windows code pages.
- `.xlsm` (macro-enabled Excel) files were silently skipped — the dispatcher
  only registered `.xlsx`. Now routed to the same `openpyxl`-based reader.
- `layout-complex` pages with embedded pictures were never OCR'd. Real-world
  testing on a screenshot-heavy slide deck (77 pages) found 76 pages
  classified `layout-complex` and only 1 ever reached OCR. That class only
  describes a busy layout (many ruling lines / fonts), not whether embedded
  pictures exist, so it now gets the same embedded-image-only OCR as `mixed`
  — native text is never touched, pages with no embedded picture still skip
  OCR entirely. On that deck, OCR coverage went from 1 page to 36.
- UI icons (sort arrows, folder/file glyphs) were being misread as random
  Chinese characters — the recognition model has no "not text" class, so an
  ambiguous icon shape gets forced into the closest CJK ideograph it knows.
  A single isolated CJK character with nothing else in the line is now
  replaced with `[icon]`, a readable marker instead of misleading noise.
- Reported OCR confidence looked far better than the text read. The page
  number is a single blended average, so one clean title averaged away a
  garbled chart axis and `OCR_LOW_CONFIDENCE` never fired. A new
  `OCR_MIXED_CONFIDENCE` warning reports how many individual lines fall below
  `--ocr-min-confidence` on a page whose average passes, plus the worst line's
  score. On the 77-page test deck: the old signal fired on 0 of 36 OCR'd
  pages; the new one flags 25, including a page reporting 96% overall while
  carrying 5 unusable lines.
- Long lines of small text lost their spaces and decoded as garbage. The cause
  was ours, not the model's: recognition crops were capped at 640 px wide, so
  a wide line was compressed up to 3x before the model saw it and neighbouring
  glyphs merged. Whole sentences came back as a few stray letters at 0.37
  confidence. The cap is now 2048 px, and crops are batched with others of
  similar width so the wider budget costs nothing — a batch pads to its widest
  member. On the 77-page test deck: OCR text went from 31,213 to 39,576
  characters (+763 words), 33 pages changed, and the run got slightly *faster*
  (1m24s to 1m20s) because width-grouped batching removes more padding waste
  than the wider crops add.
- Page confidence averaged per image instead of per line, so a one-line
  thumbnail counted as much as a fifty-line screenshot on the same page.
  Now averaged over lines. `OcrResult.mean_confidence()` is replaced by
  `OcrResult.scores()`, which hands the caller the per-line values to pool.

## Phase 1.0 — Initial Pipeline (baseline)

The original extraction pipeline, delivering core document-to-JSON/Markdown conversion.

### Features

- **PDF extraction** — PyMuPDF for text + metadata + image counts, pdfplumber for table detection
- **Table de-duplication** — bounding-box exclusion prevents table text from appearing in both text and table blocks
- **Table false-positive filtering** — rejects detections with < 2 cols or < 2 rows
- **CSV extraction** — stdlib csv, delimiter sniffing, one table block per file
- **DOCX extraction** — python-docx, paragraphs and tables in document order
- **XLSX extraction** — openpyxl, one sheet unit per worksheet, computed values (not formulas), empty sheets skipped
- **Text normalisation** — fix hyphenation, collapse spaces, remove empty lines, trim
- **JSON + Markdown writers** — internal model serialised to both formats
- **Reliability isolation** — page/table/document failures logged and skipped, batch never aborts
- **Runtime-generated test fixtures** — no binaries committed, 48 tests

### Architecture

- `main.py` — orchestrator (discovers files, processes, logs summary)
- `extractor/dispatcher.py` — routes by extension to format reader
- `extractor/model.py` — single source of model shape
- `extractor/normalizer.py` — text cleaning rules
- `extractor/pdf_reader.py`, `csv_reader.py`, `docx_reader.py`, `xlsx_reader.py`
- `extractor/table_reader.py` — pdfplumber table extraction
- `extractor/json_writer.py`, `markdown_writer.py`

---

## Phase 1.1 — Extraction Fidelity, Formats & Dev Quality

Implemented from `implementation-plan.md`. Adds PDF intelligence, six new formats, dev tooling, and a quality harness.

### Task 1: pyproject.toml + ruff + coverage

- `pyproject.toml` with pinned runtime deps, `[dev]` extra (pytest, pytest-cov, ruff)
- Ruff config: BLE, isort, bugbear, pyupgrade, simplify rules
- Pytest config: testpaths, strict-markers, coverage defaults
- `requirements.txt` as thin pointer (`-e .[dev]`)
- Fixed 3 lint findings in existing code (no behaviour change)

### Task 2: CLI arguments + input limits

- `extractor/limits.py` — `MAX_FILE_BYTES`, `MAX_PAGES`, `FileTooLargeError`, `check_file_size()`
- argparse in `main.py`: `--input`, `--output`, `--log-file`, `--max-file-mb`, `--max-pages`, `--verbose`
- Oversized files skipped with warning, counted as failures, batch continues
- `main()` accepts `argv` parameter for testability

### Task 3: Model extension

- `schema_version` (`"2.0"`) always present on document node
- `warnings` list on document node (omitted when empty)
- `page_class` per unit (optional, PDF-only)
- `make_header_block()` / `make_footer_block()` constructors
- `markdown_writer` skips `header`/`footer` block types

### Task 4: Encoding detection + Unicode repair

- `extractor/text_loader.py` — `load_text_file()` using charset-normalizer, returns `LoadResult` with text + encoding + confidence + warnings
- `ENCODING_FALLBACK` warning on low confidence
- `normalizer.normalize_with_report()` — ftfy repair as step 1, reports `UNICODE_REPAIRED`
- `csv_reader` uses encoding-aware loader (replaces hardcoded utf-8)

### Task 5: PDF page classification

- `extractor/page_signals.py` — `PageSignals` dataclass, `compute_signals()`, `classify_page()`, `warnings_for_page()`
- Classifications: `native-text`, `scanned`, `mixed`, `garbled`, `layout-complex`
- Warning codes: `SCANNED_PAGE_NO_TEXT`, `MIXED_CONTENT_PAGE`, `GARBLED_TEXT`, `LAYOUT_COMPLEX`
- Wired into `pdf_reader`: `page_class` per unit, warnings aggregated to document

### Task 6: Header/footer detection

- `extractor/headers_footers.py` — signature-based detection (top/bottom 12% band, digit normalisation, 60% occurrence threshold, minimum 3 pages)
- Detected lines emitted as `header`/`footer` blocks, removed from text content
- `HEADER_FOOTER_DETECTED` document-level warning
- Documents under 3 pages left untouched

### Task 7: Column-aware reading order

- `extractor/reading_order.py` — `WordInfo`/`Block` dataclasses, `_cluster_into_columns()`, `reorder_words()`
- Greedy x-overlap clustering (threshold 0.40), columns ordered left-to-right
- `POSSIBLE_TWO_COLUMN_ORDER` warning for multi-column pages
- Single-column pages produce same order as PyMuPDF default (regression guard)

### Task 8: Plain text + Markdown readers

- `extractor/txt_reader.py` — encoding-aware, one section unit
- `extractor/md_reader.py` — pass-through (source preserved faithfully), one section unit
- Registered `.txt`, `.md` in dispatcher

### Task 9: HTML reader

- `extractor/html_reader.py` — BeautifulSoup + lxml (fallback to html.parser)
- Strips `script`, `style`, `nav`, comments
- Headings/paragraphs → text blocks, `<table>` → table blocks (ragged rows padded)
- Registered `.html`, `.htm`

### Task 10: PowerPoint reader

- `extractor/pptx_reader.py` — python-pptx, one `slide` unit per slide
- Text frames → text blocks, graphic_frame tables → table blocks, speaker notes included
- `slide` added to `markdown_writer._UNIT_LABELS`
- Registered `.pptx`

### Task 11: JSON + XML readers

- `extractor/json_reader.py` — flat records → table block; nested → indented text; malformed → ValueError
- `extractor/xml_reader.py` — defusedxml (XXE refused), rendered as indented path/text lines
- Registered `.json`, `.xml`

### Task 12: Golden corpus + semantic metrics

- `tests/golden/conftest.py` — 11 runtime-generated fixtures (single-column, two-column, image-only, mixed, repeated-header, unicode PDFs; nested-list/table-between DOCX; merged-cell/hidden-sheet/dates XLSX)
- `tests/metrics.py` — phrase_retention, duplicate_line_ratio, table_cell_recall, table_cell_precision, reading_order_errors, header_footer_leakage, empty_page_rate, unicode_corruption_rate
- `tests/golden/test_golden.py` — 27 threshold assertions per corpus document

### Task 13: OCR PRD

- `PRD - Faza 1.2.md` rewritten as complete OCR specification (scope, pluggable engine, pytesseract default, EasyOCR opt-in, `image_ocr` block type, trigger integration, module layout)
- `PRD - Faza2.md` formatting repaired

### Task 14: Documentation + verification

- `README.md` refreshed (format table, warning codes, CLI usage, output shape, architecture diagram, dependency notes)
- `CLAUDE.md` refreshed (new modules, conventions, testing section)
- Full suite green (75 tests), ruff clean, end-to-end pass over `docs/` + `input/`

---

## Phase 1.1a — Code Quality & Testing Improvements

Post-implementation polish applied via python-patterns and python-testing skill reviews.

### Code quality fixes

- Removed unused `from pptx.util import Inches` import
- Replaced identity comprehension `[cell for cell in row]` with `list(row)`
- Added `path: Path | str` type annotations to all 12 reader functions + dispatcher
- Extracted `_rects_overlap` to `extractor/geometry.py` (eliminates duplication)
- Replaced `try/finally: doc.close()` with `with fitz.open(path) as doc:` context manager
- Added `@dataclass(slots=True)` to `PageSignals` for memory efficiency

### Test suite expansion (75 → 120 tests)

- `tests/test_limits.py` — 7 tests (boundary checks, error context, default max_bytes)
- `tests/test_text_loader.py` — 5 tests (UTF-8, empty, latin-1, cp1250, dataclass)
- `tests/test_normalizer_extended.py` — 7 tests (normalize_with_report, mojibake, parametrized)
- `tests/test_new_readers.py` — 22 tests (txt, md, html, pptx, json, xml + edge cases + parametrized source_type)
- `tests/test_geometry.py` — 8 parametrized overlap tests
- `tests/test_cli.py` — 10 tests (parser defaults/overrides, integration with oversized/empty/valid files)
- Registered `slow` and `integration` markers in `pyproject.toml`
- Coverage: 91.7%

---

## Not yet implemented (future work)

| Item | Spec | Notes |
|------|------|-------|
| OCR module | `PRD - Faza 1.2.md` | Standalone images + embedded PDF images, pytesseract + EasyOCR, behind `--ocr` |
| Phase 2: Indexing | `PRD - Faza2.md` | Chunking, embeddings, ChromaDB, semantic search |
| Legacy binary formats | — | `.doc`, `.xls`, `.ppt` not supported |
| mypy type checking | — | Type hints present but not enforced by checker |
| Docling/MinerU adapters | — | Deliberately left out |
| NDJSON checkpoint-resume | — | Deliberately left out |
| Pydantic model migration | — | Deliberately left out |
| `--debug-layout` rendering | — | Deliberately left out |
| python-magic sniffing | — | Deliberately left out |
| tiktoken counts | — | Deliberately left out |
| hypothesis property testing | — | Deliberately left out |
| pip-audit | — | Deliberately left out |
