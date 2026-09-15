"""Turn PaddleOCR-VL's Markdown output into internal-model content.

The doctag sibling of this module parses a tag stream; this one parses what a
document-conversion model writes when asked for Markdown: pipe tables, the
occasional HTML ``<table>``, display formulas in ``$$``/``\\[`` delimiters, and
prose. It produces the same ``ParsedPage`` and reuses the same formula
judgement, so ``apply.py`` never learns which model it is holding.

Order matters in ``parse``: layout tokens are stripped before anything
else reads the text, formulas are lifted out before tables, because a
``\\left|`` inside an equation would otherwise read as a table column, and
tables are lifted out before prose, because cell text scanned as prose is
emitted a second time.
"""
from __future__ import annotations

import re

from extractor.vlm.doctag import ParsedPage, formula_is_balanced

# Display math only. Inline ``$x$`` inside a sentence is part of that sentence
# and is left in the prose, where it already reads correctly.
_FORMULA = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)

_HTML_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.DOTALL | re.IGNORECASE)
_HTML_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_HTML_CELL = re.compile(r"<(t[dh])[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")

# A row of dashes under the header is what separates a real pipe table from a
# sentence that happens to contain a vertical bar. It is required, not
# optional: without it any prose line with a pipe becomes a one-row table.
_SEPARATOR = re.compile(r"^[\s|:-]*-[\s|:-]*$")

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b", re.IGNORECASE)

# The model interleaves layout tokens with the prose on some pages — 120 of
# them on one measured page of the reference course, all of which reached the
# parsed text before this was added. They carry coordinates we do not use (block
# order alone is the reading order we need), and the generic markup strip does
# not reach them because it only runs inside HTML cells.
_SPECIAL = re.compile(r"<\|[^|>]*\|>")


def strip_special_tokens(raw: str) -> str:
    """Drop the model's ``<|LOC_n|>`` layout tokens from *raw*."""
    return _SPECIAL.sub("", raw)


def _clean_cell(html: str) -> str:
    """Strip markup from one HTML cell's contents."""
    return _TAG.sub("", html).replace("&nbsp;", " ").strip()


def _split_row(line: str) -> list[str]:
    """Split one pipe-table line into stripped cells, dropping the outer pipes."""
    cells = line.split("|")
    if cells and not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return [cell.strip() for cell in cells]


def _take_html_tables(body: str) -> tuple[list[list[list[str]]], str]:
    """Return the HTML tables in *body* and *body* with them removed."""
    tables = []
    for match in _HTML_TABLE.finditer(body):
        rows = [
            [_clean_cell(cell) for _, cell in _HTML_CELL.findall(row)]
            for row in _HTML_ROW.findall(match.group(1))
        ]
        rows = [row for row in rows if row]
        if rows:
            tables.append(rows)
    return tables, _HTML_TABLE.sub("", body)


def _take_pipe_tables(body: str) -> tuple[list[list[list[str]]], str]:
    """Return the pipe tables in *body* and *body* with them removed.

    A run of consecutive lines containing a pipe is a candidate; it only
    becomes a table once a separator row is found inside it.
    """
    tables: list[list[list[str]]] = []
    kept: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if any(_SEPARATOR.match(line) for line in block):
            rows = [
                _split_row(line) for line in block if not _SEPARATOR.match(line)
            ]
            rows = [row for row in rows if any(cell for cell in row)]
            if rows:
                tables.append(rows)
        else:
            kept.extend(block)
        block.clear()

    for line in body.splitlines():
        if "|" in line:
            block.append(line)
            continue
        if block:
            flush()
        kept.append(line)
    if block:
        flush()

    return tables, "\n".join(kept)


def is_truncated(raw: str) -> bool:
    """Markdown carries no closing-tag evidence, so this adds nothing.

    The doctag sibling can prove truncation by unbalanced tags. Prose has no
    equivalent — an answer cut mid-sentence is shaped exactly like one that
    ended there. The real guard for this format is the engine's token-cap
    signal in ``apply.py``, which is evidence rather than inference and covers
    both models; returning False here defers to it rather than inventing a
    heuristic that cannot tell.
    """
    return False


def parse(raw: str) -> ParsedPage:
    """Translate one page of Markdown output into prose, tables and counts."""
    result = ParsedPage()
    # First, so a layout token inside an equation cannot corrupt its LaTeX.
    body = strip_special_tokens(raw).strip()
    result.has_picture = bool(_IMAGE.search(body))

    pieces: list[str] = []

    def take_formula(match: re.Match) -> str:
        latex = (match.group(1) or match.group(2)).strip()
        if not latex:
            return ""
        if not formula_is_balanced(latex):
            # Dropped, not emitted broken: a wrong equation that renders is
            # worse than a missing one, because nothing flags it.
            result.rejected_formulas += 1
            return ""
        result.formula_count += 1
        # Stored bare. apply.py adds the delimiters itself when it rebuilds a
        # redundant page from formulas alone; storing them here double-wraps.
        result.formulas.append(latex)
        return f"\x00{len(result.formulas) - 1}\x00"

    body = _FORMULA.sub(take_formula, body)

    html_tables, body = _take_html_tables(body)
    pipe_tables, body = _take_pipe_tables(body)
    result.tables = html_tables + pipe_tables

    for line in body.splitlines():
        line = _IMAGE.sub("", line).strip()
        if not line:
            continue
        pieces.append(line)

    text = "\n".join(pieces)
    for index, latex in enumerate(result.formulas):
        text = text.replace(f"\x00{index}\x00", f"$$\n{latex}\n$$")
    result.text = text
    return result
