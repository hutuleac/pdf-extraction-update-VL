# Desktop Launcher + Local Web UI — Design

Date: 2026-08-31
Status: Approved for implementation planning

## 1. Purpose

Let a non-technical colleague convert a folder of mixed documents into JSON and
Markdown by double-clicking one file. No terminal, no Python knowledge, no
manual setup. The output feeds their own LLM workflow, so the result must land
in a place they can find and open immediately.

Today the pipeline is a CLI: it needs a virtual environment, an editable
install, an `input/` folder, and a command line. Each of those is a stop point
for the target user.

## 2. Non-goals

- No change to extraction quality, output shape, or any reader.
- No multi-user, network, or hosted deployment. One machine, one user, loopback
  only.
- No packaged `.exe`, installer, or code signing.
- No new document formats.
- No queueing, scheduling, or run history beyond the current session.

## 3. Guiding constraint: the existing pipeline is frozen

Nothing under `extractor/` is modified. `main.py` is not modified either.

The UI **imports** `process_file` from `main.py` — the same extraction and
writing sequence the CLI runs — plus `check_file_size`, `configure`,
`SUPPORTED_EXTENSIONS`, `describe`, and the OCR `registry` probe from their own
modules.

It deliberately does **not** import `main.file_stats` or
`main.discover_inputs`. `ui/quality.py` supersedes the first (it needs
characters, durations and a verdict, which `file_stats` does not carry) and
`ui/selection.py` supersedes the second (it must also report the files it
ignored). Reusing them and then bolting the missing half on beside them would
leave two half-answers where the UI needs one.

Consequences, and the reason for the rule:

- A green CLI test run guarantees the UI runs the same extraction code.
- Deleting `Start.bat`, `launcher/`, and `ui/` returns the repo to its current
  state exactly.
- A UI defect can never corrupt extraction output, because the UI contributes
  no extraction logic.

`main.py` is importable as-is (its work is behind `if __name__ == "__main__"`),
so this needs no refactor.

## 4. Architecture

```
Start.bat                  double-click entry point
launcher/
  setup.ps1                idempotent environment bootstrap
ui/
  __init__.py
  server.py                stdlib HTTP server + routes
  jobs.py                  worker thread, progress state, results
  selection.py             folder/file scan, supported vs ignored, output paths
  quality.py               stats + pass/review/fail gates
  report.py                writes report.md / report.json beside the output
  picker.py                native Windows file/folder dialogs
  static/
    index.html
    app.js
    style.css
tests/
  test_ui_jobs.py
  test_ui_quality.py
  test_ui_server.py
  test_ui_picker.py
```

### 4.1 Web stack: Python standard library only

`http.server.ThreadingHTTPServer` with a hand-written route table. No Flask, no
FastAPI, no uvicorn.

Rationale: the bootstrap then installs only what the pipeline already declares.
Every added dependency is another download that can fail on a locked-down
corporate machine on first run — the exact moment the user has the least
patience and the least ability to diagnose. A single-user local tool has no
traffic that a heavier server would serve better.

Progress reaches the page by polling `GET /api/status` every 500 ms. Polling
loopback at that rate is free and removes the WebSocket reconnect logic.

### 4.2 Module responsibilities

**`ui/picker.py`** — opens the real Windows dialogs.

`pick_folder() -> str | None` and `pick_files() -> list[str]`. Each spawns a
short-lived `sys.executable` subprocess that runs a tiny tkinter snippet and
prints the selection to stdout.

The subprocess is not an implementation detail to optimize away later: tkinter
must own the main thread, and the HTTP server already owns it. Running dialogs
out of process keeps the server responsive, keeps a crashed dialog from taking
the app down, and makes the module trivially stubbable in tests. It also means
tkinter is required — which is why the bootstrap installs a real Python
distribution and never the embeddable ZIP, which omits tkinter.

**`ui/jobs.py`** — owns one conversion run.

- `start(paths: list[Path], config: dict) -> str` returns a job id and starts a
  daemon worker thread.
- The worker calls `configure(...)` once, then per file: `check_file_size`,
  `process_file`, `file_stats` — the same sequence and the same error isolation
  as `main.py`'s loop. One file's failure is caught, recorded, and the run
  continues.
- `status(job_id) -> dict` returns the progress and results shape described in
  section 6, under a lock.
- `stop()` sets a flag the worker checks between files (see section 6.5).

Progress granularity is per file. The pipeline exposes no intra-file progress,
and inventing one would mean touching the readers. Section 6.1 explains what
is shown instead.

**`ui/quality.py`** — turns a finished document into numbers and a verdict.
Pure functions over the model dict, no I/O, no threads. Section 6 defines the
rules; keeping them in one module with no dependencies is what makes them
testable and what stops the same threshold being written twice.

**`ui/report.py`** — writes `report.md` and `report.json` into the `Extracted`
folder at the end of a run, from the same result list the page renders.

**`ui/server.py`** — routes and process lifetime.

| Route | Purpose |
|---|---|
| `GET /` | the single page |
| `GET /static/*` | assets |
| `GET /api/env` | OCR availability and the reason when it is off |
| `POST /api/pick-folder` | native folder dialog → `{path, file_count}` |
| `POST /api/pick-files` | native file dialog → `{paths, file_count}` |
| `POST /api/convert` | body `{paths}` → `{job_id}` |
| `GET /api/status?job=<id>` | job progress and results |
| `POST /api/stop` | finish the current file, then end the run |
| `POST /api/open-folder` | `explorer.exe <path>` on a completed run |
| `POST /api/open-file` | open one result's Markdown in the default editor |
| `POST /api/quit` | orderly shutdown (sent by the page on unload) |

`file_count` is computed with `SUPPORTED_EXTENSIONS`, so the page can say
"14 supported files found" before anything runs, and can say "0 supported
files found" instead of starting an empty job. The same scan returns the
**ignored** files and their extensions, so the page can answer "where is my
`.doc` file?" before the user has to ask it.

`/api/open-file` and `/api/open-folder` accept an index into the current
session's result list, never a path from the request body. Nothing the browser
sends is ever passed to the shell.

### 4.3 Output location

`<source folder>\Extracted\json\` and `<source folder>\Extracted\markdown\`.

For individually picked files, each file's output goes beside that file's own
folder. A single run can therefore write to several `Extracted` folders; the
results list names the folder per file, and **Open results folder** opens the
first one.

Existing files are overwritten, matching current CLI behaviour.

### 4.4 Security

- Bind `127.0.0.1` on a random free port. Never `0.0.0.0`.
- Generate a random token at startup; the browser is opened at
  `http://127.0.0.1:<port>/?t=<token>`, and every `/api/*` request must carry
  it. Without this, any local process or any page in the user's browser could
  drive the file dialogs and the converter.
- `POST /api/open-folder` only accepts a path this session produced. Paths are
  never taken from the request body for shell execution.
- The server exits on `/api/quit`, and after 30 minutes with no request, so a
  forgotten tab does not leave a listener running.

## 5. Bootstrap (`Start.bat` → `launcher/setup.ps1`)

`Start.bat` is a three-line shim that calls PowerShell with the execution
policy bypassed for that single process. All logic lives in `setup.ps1`, where
it can be read and maintained.

Every step checks before it acts, so the second run costs about a second.

1. **Already good?** If `.venv\Scripts\python.exe` exists and
   `-c "import fitz, docx, openpyxl, ui"` succeeds → go to step 6.
2. **Find Python 3.11+.** Try `py -3.13`, `py -3.12`, `py -3.11`, then
   `python`, verifying the version. Found → step 4.
3. **Install Python.** `winget install --id Python.Python.3.12 --scope user`
   (no administrator rights needed). If `winget` is absent, download the
   official python.org installer and run it with
   `InstallAllUsers=0 PrependPath=1 Include_tcltk=1 /quiet`. If both fail,
   print one short message naming python.org and stop. This step is the only
   one that needs internet, and only once.
4. **Create `.venv`** with the found interpreter.
5. **Install dependencies.** `pip install -e ".[ocr]"`. If the OCR extra fails
   (no wheel for the platform, blocked index), fall back to `pip install -e .`
   and record that OCR is off. A machine that cannot install onnxruntime should
   still convert its Word and Excel files.
6. **Check OCR models.** If `models/ocr/*.onnx` are missing (a clone without
   `git lfs pull`), continue and let the UI show "OCR unavailable — scanned
   pages and images will not be read".
7. **Launch.** Start `python -m ui.server`, which opens the default browser at
   the tokenized URL. The console window stays open as the visible "running"
   indicator, with one line telling the user that closing it stops the app.

Every failure path ends in a sentence a non-technical user can act on or
forward, never a Python traceback. The full detail goes to
`logs/launcher.log`.

## 6. Progress, timing, stats, and quality gates

The user's output feeds their own LLM. The question they need answered is not
"did it finish?" but "**can I trust this, and what should I look at first?**"
Everything in this section serves that question. Anything that does not is out.

### 6.1 Progress

Two indicators, both truthful.

**Overall bar, weighted by byte size.** `done_bytes / total_bytes`, not
`done_files / total_files`. A folder of 13 small Word files and one 40 MB
scanned PDF would otherwise reach 93% in two seconds and then sit still for
four minutes. Size is a rough proxy for work, but it is a far better one than
file count, and it costs one `stat()` per file.

**Per-file: an elapsed timer, not a bar.** While a file is processing the page
shows its name and a live seconds counter. There is deliberately no per-file
percentage: the readers expose no intra-file progress, and adding a progress
callback to `pdf_reader` would break the freeze rule in section 3. A counter
that is honest beats a bar that is invented. For the one case where a file can
legitimately take minutes — OCR on a large scan — the counter plus the
filename is exactly the reassurance needed.

**ETA.** Remaining bytes divided by bytes-per-second achieved so far, shown
only after the first file completes and only rounded ("about 2 min left").
Before then there is no data to estimate from, and a wrong first estimate is
worse than none.

### 6.2 Timing

- Per file: wall-clock seconds, kept in the result row and in the report.
- Per run: total wall clock, plus the slowest file named.

Duration is not decoration here. A 200 KB PDF that takes three minutes means
OCR is grinding on it, which is itself a quality signal worth surfacing.

### 6.3 Output stats

Per file, computed in `ui/quality.py` from the model that `process_file`
already returns — no second pass over anything:

| Stat | Source |
|---|---|
| units (pages / sheets / slides) | `document.pages` |
| text blocks, table blocks | count over `unit.content` by `type` |
| **characters extracted** | sum of `len(block["content"])` over text blocks |
| table cells | sum of cell counts over table blocks |
| images found | `document.image_count` |
| pages read by OCR | count of `OCR_APPLIED` warnings |
| average OCR confidence | mean `confidence` over OCR blocks, when any |
| estimated tokens | `characters / 4`, labelled *approximate* |
| output size | bytes of the written `.json` and `.md` |

**Characters extracted is the headline number.** It is the one figure that
separates "converted" from "converted to nothing", and it is not in the
existing `file_stats()`. Estimated tokens is a crude `chars / 4` and the UI
says so; it is included only because the user's next step is chunking for an
LLM, where an order of magnitude is genuinely useful and precision is not.

Run totals are the sums, plus the file count by verdict.

### 6.4 Quality gates

Every file gets exactly one verdict. The rules live in one table in
`ui/quality.py` so a new warning code slots into a known place instead of being
handled ad hoc in the UI.

**FAIL** — the file did not produce usable output:

- the reader raised, or the file was skipped for size;
- **zero characters and zero table cells extracted** — a "successful" empty
  conversion is the silent failure this whole feature exists to catch;
- more than half the units carry an unreadable code (`OCR_UNAVAILABLE`,
  `OCR_SKIPPED_DISABLED`, `OCR_MODEL_INCOMPATIBLE`, `OCR_FAILED`).

**REVIEW** — output exists but something about it is not trustworthy:

- any of `OCR_LOW_CONFIDENCE`, `OCR_MIXED_CONFIDENCE`, `OCR_NOISE_FILTERED`,
  `GARBLED_TEXT`, `ENCODING_FALLBACK`;
- at least one but not most units unreadable;
- average OCR confidence below the configured minimum.

Page-limit truncation is **not** a trigger. `MAX_PAGES` is defined and
`--max-pages` is parsed, but the value reaches no reader — nothing truncates,
so nothing warns. A gate written for it would be dead code pretending to be a
safeguard. Fixing that is a pipeline change and out of scope here.

**PASS** — everything else. Notes like `HEADER_FOOTER_DETECTED` or
`LAYOUT_COMPLEX` are informational and still shown, but they do not downgrade
a file. A gate that fires on normal documents trains the user to ignore it.

The run headline is one sentence: *"14 files: 11 clean, 2 need review,
1 failed."* Review and failed rows sort to the top, because a list sorted
alphabetically hides exactly the rows that need attention.

**Preview snippet.** Each row carries the first ~200 characters of extracted
text. It is the fastest possible trust check — garbled OCR is obvious at a
glance, and it needs no file to be opened. For a document set that is largely
scans, this is worth more than every other statistic combined.

### 6.5 Stopping a run

**Stop** finishes the file in flight, then ends the run and reports what was
completed. Killing a worker thread mid-extraction risks half-written output;
finishing one file is bounded and leaves the output directory consistent.

## 7. Report file

At the end of every run, `ui/report.py` writes `report.md` and `report.json`
into the `Extracted` folder: the run headline, the per-file table (verdict,
duration, stats, notes), and the settings used.

Two reasons this is worth the ~60 lines. The user's own LLM can read
`report.json` to decide which files to re-check before ingesting them. And when
something looks wrong, they can send one file that contains the full picture
instead of describing a screen from memory.

## 8. The screen

One page, no navigation, three states.

**Idle.** Title, one sentence of purpose, two buttons: *Choose folder…* and
*Choose files…*. Below them, once a selection exists: the chosen path,
"N supported files found", and — only when there are any — a collapsed
"M files ignored" line listing their extensions. Then a primary **Convert**
button. A quiet line reports OCR availability.

**Running.** The weighted progress bar, "file 4 of 14", the current filename
with its live elapsed timer, the rounded ETA, and the result rows filling in
as files complete. Convert is replaced by **Stop**.

**Done.** The run headline — *"14 files: 11 clean, 2 need review, 1 failed"* —
total duration, and the run totals (characters, pages, tables, approximate
tokens). Then the result list, review and failed rows first. Each row shows:
verdict mark, filename, duration, the key stats, the plain-language notes from
`describe()`, and the preview snippet. Clicking a row opens its Markdown.
Failed rows show the error sentence. Three buttons: **Open results folder**,
**Convert something else**, and — when anything failed — **Retry failed only**.

Detail is layered, not dumped: each row shows verdict, name, duration and one
line of notes by default, and expands to the full statistics table. A
non-technical user must be able to read the headline and stop there.

Wording is the existing `warning_text` wording. That module already exists to
say these things once for the Markdown writer and the CLI summary; the UI is
its third consumer, not a fourth vocabulary.

Visual language follows the house style: minimal, high whitespace, system font
stack, one accent colour, works in light and dark via `prefers-color-scheme`.
No framework, no CDN — the page must render with the machine offline.

## 9. Advanced options

Not in this version. OCR runs when available, defaults come from
`extractor/ocr/config.py` and `extractor/limits.py`.

The `POST /api/convert` body carries a `config` object from the start, so
exposing DPI, confidence, or size limits later is a UI change only.

## 10. Explicitly out of scope

Named here so they are decisions, not oversights:

- **Run history or a database.** Each run stands alone; `report.json` is the
  record.
- **Charts or a dashboard.** This is a converter. Numbers in a table are read
  faster than numbers in a donut.
- **Page thumbnails or a side-by-side original/extracted viewer.** Large build,
  and the preview snippet plus one click to the Markdown already answers
  "is this right?".
- **Cancel mid-file.** Only stop-after-current-file, per section 6.5.
- **Notifications, email, scheduling.** The console window is the indicator.
- **Per-page progress inside a file.** Would require touching the readers.

## 11. Testing

New tests, all fast, none requiring a browser or a click:

- **`test_ui_jobs.py`** — a stub `process_file` proves: progress advances by
  bytes; one file raising does not stop the run; oversized files are recorded
  as skipped; durations are recorded; **Stop** ends the run after the current
  file; output paths resolve to `Extracted/` beside each source.
- **`test_ui_quality.py`** — the gate table, against hand-built model dicts:
  an empty document is FAIL, not PASS; a low-confidence OCR document is
  REVIEW; `HEADER_FOOTER_DETECTED` alone stays PASS; character and table-cell
  counts are right for mixed content; an unknown warning code does not crash
  the verdict.
- **`test_ui_server.py`** — routes return the right status codes and shapes;
  a request without the token is rejected; `/api/open-folder` and
  `/api/open-file` refuse an index outside the session's own results; an empty
  selection does not start a job.
- **`test_ui_picker.py`** — the dialog subprocess is stubbed; verifies cancel
  returns `None` / `[]` and that no path is fabricated.

The empty-document-is-FAIL case is the single most important test here: it is
the failure mode the whole quality layer exists to catch, and the one a
"successful" run would otherwise hide.

`setup.ps1` is not unit tested. It is verified by hand on a clean Windows
profile, twice, to confirm the second run is a no-op.

The existing suite must pass unchanged. The baseline snapshot and golden
corpus assertions are untouched, which is the objective proof that the frozen
pipeline stayed frozen.

## 12. Documentation

- `README.md`: a short "For non-technical users" section at the top —
  double-click `Start.bat`, pick a folder, click Convert, find `Extracted`.
- `CLAUDE.md`: one paragraph placing `ui/` and `launcher/` as a layer above the
  pipeline, and stating the rule that they import from `main.py` rather than
  reimplement it.

## 13. Risks

| Risk | Handling |
|---|---|
| Corporate policy blocks `winget` and python.org | Detected, reported in one sentence with the manual step. Documented as a known limitation. |
| PowerShell execution policy blocks `setup.ps1` | `Start.bat` passes `-ExecutionPolicy Bypass` for that process only, which does not need admin and does not change machine policy. |
| Antivirus flags a `.bat` that downloads an installer | Only triggers on a machine with no Python. Documented; the manual install path stays available. |
| tkinter missing | Prevented by installing a real distribution with `Include_tcltk=1`, never the embeddable ZIP. Detected at startup with a clear message. |
| Long run looks frozen | Byte-weighted bar, the current filename, a live elapsed timer, and an ETA. A single huge scan still shows a moving timer, which is the honest signal. |
| User closes the console window mid-run | The run stops. The console states this on line one. Partial output already written stays on disk, and **Stop** is offered as the clean alternative. |
| Byte-weighted progress misleads on a small dense PDF | Accepted. Size is a proxy, not a measure; the ETA is rounded and hedged ("about"), and the elapsed timer is always exact. |
| Quality gates fire on ordinary documents and get ignored | `HEADER_FOOTER_DETECTED`, `LAYOUT_COMPLEX` and `POSSIBLE_TWO_COLUMN_ORDER` are informational and never downgrade a file. Verified by `test_ui_quality.py`. |
| Estimated tokens read as exact | Always shown with "approx." and defined in the report as `characters / 4`. |

## 14. Definition of done

- Double-clicking `Start.bat` on a Windows machine with no Python ends with a
  browser page ready to convert.
- Running it a second time reaches the same page in about a second.
- A folder of mixed formats converts, and `Extracted/json`,
  `Extracted/markdown` and `Extracted/report.md` appear beside the source
  files.
- Warnings appear on screen as sentences, not codes.
- Every file carries a verdict, a duration, and the stats from section 6.3;
  a document that extracts nothing is reported as failed, not as converted.
- The full existing test suite passes unchanged, baseline snapshot included.
- `ruff check .` is clean.
