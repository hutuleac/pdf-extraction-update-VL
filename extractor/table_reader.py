"""Table extraction via pdfplumber (FR4).

Returns both the extracted cells and each table's bounding box, so the text
pass (PyMuPDF) can exclude table regions and avoid duplicating table content.
"""
import logging
from pathlib import Path
from statistics import median

import pdfplumber

from extractor.rotated_text import keep_char, repeated_vertical_boxes

logger = logging.getLogger(__name__)


_MIN_COLS = 2
_MIN_ROWS = 2

# A grey title bar over a content box — the layout of a lecture slide — reads to
# pdfplumber's line detection as a 2x2 grid, so a whole slide body lands in one
# cell joined by newlines (124 of 282 detections on the 388-page reference
# document, 289K of its 729K text chars). Shape alone cannot tell those apart
# from a real table; cell *length* can. Measured on that document, a real table's
# median non-empty cell is 2-14 characters and a prose box's is 200-1900, so the
# gap between them is two orders of magnitude wide. Rejecting here also returns
# the region to the PyMuPDF text pass, which reads it as prose in reading order
# and applies the word-level rotated-text filter the cell path lacks.
# ponytail: threshold calibrated on one slide-deck PDF; a spec table with a long
# free-text Notes column could trip it. Widen by looking at the shortest column
# rather than all cells if that shows up.
_MAX_MEDIAN_CELL_CHARS = 120

# pdfplumber's own default (3) treats a real word gap as a glued run when the
# source PDF kerns tightly (measured ~2.5-2.7pt word gaps against ~0pt
# intra-word gaps in a justified textbook layout) — "coeficientulluiHazen"
# instead of "coeficientul lui Hazen". 1.5 sits well below the smallest real
# word gap seen and well above intra-word kerning, so it doesn't cost the
# glued-word fix wrongly splitting a real word.
_CELL_TEXT_X_TOLERANCE = 1.5


def _in_any_box(char, boxes) -> bool:
    """True if a char's center falls inside one of a page's stamp boxes."""
    cx = (char["x0"] + char["x1"]) / 2
    cy = (char["top"] + char["bottom"]) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)


def _clean_row(row) -> list[str]:
    """Normalize a raw row: None cells -> '', everything cast to str."""
    return ["" if cell is None else str(cell) for cell in row]


# A ruled chart (e.g. a geological time-scale diagram) reads to pdfplumber's
# line detection as a table grid, and a text label overlapping the whole grid
# gets assigned to every cell it touches — a header row followed by dozens of
# identical data rows. A real table's rows vary; reject this shape rather than
# publish a repeated garbage row N times. Below this many data rows a genuine
# small table can legitimately repeat a value in every row (e.g. a single
# "Yes" column), so only larger repeats are assumed to be the chart artifact.
_MIN_ROWS_FOR_IDENTICAL_CHECK = 3


def _all_data_rows_identical(cells: list[list[str]]) -> bool:
    data_rows = cells[1:]
    if len(data_rows) < _MIN_ROWS_FOR_IDENTICAL_CHECK:
        return False
    return len({tuple(row) for row in data_rows}) == 1


def _is_real_table(cells: list[list[str]]) -> bool:
    """A real table has at least 2 columns and 2 rows, and cells that hold
    values rather than paragraphs. Single-column, single-row, and prose-filled
    detections are pdfplumber false positives (prose caught by line detection)
    and are rejected so the text stays as prose."""
    if len(cells) < _MIN_ROWS:
        return False
    max_cols = max((len(row) for row in cells), default=0)
    if max_cols < _MIN_COLS:
        return False
    filled = [text for row in cells for cell in row if (text := cell.strip())]
    if not filled:
        return False
    if median(len(text) for text in filled) >= _MAX_MEDIAN_CELL_CHARS:
        return False
    return not _all_data_rows_identical(cells)


def extract_tables(pdf_path) -> dict[int, list[dict]]:
    """Return {page_number(1-based): [table_entry, ...]} for pages with tables.

    Each table_entry is {"cells": [...], "bbox": (x0, top, x1, bottom),
    "origin": (x, y)}. `bbox` is in pdfplumber's coordinate space, which shares
    an origin with PyMuPDF's only on an upright page whose MediaBox starts at
    (0, 0). `origin` is that page's pdfplumber bbox corner, which is what the
    PDF reader needs to translate the box into word coordinates before using it
    to exclude text.

    Per-page and per-table failures are isolated and logged.
    """
    pdf_path = Path(pdf_path)
    result: dict[int, list[dict]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        stamp_boxes_by_page = repeated_vertical_boxes(pdf)
        for index, raw_page in enumerate(pdf.pages):
            page_number = index + 1
            # A stamped watermark scatters its glyphs through the cells of every
            # table it crosses. Dropping it here keeps the ruling lines the
            # table is detected from, so only the cell text changes.
            stamp_boxes = stamp_boxes_by_page.get(page_number)
            if stamp_boxes:
                page = raw_page.filter(
                    lambda obj, boxes=stamp_boxes: keep_char(obj) and not (
                        obj.get("object_type") == "char" and _in_any_box(obj, boxes)
                    )
                )
            else:
                page = raw_page.filter(keep_char)
            try:
                found = page.find_tables()
            except Exception as exc:  # noqa: BLE001 - reliability: isolate page
                logger.warning("Table extraction failed on page %d: %s", page_number, exc)
                continue
            entries: list[dict] = []
            for table in found:
                try:
                    cells = [
                        _clean_row(row)
                        for row in table.extract(x_tolerance=_CELL_TEXT_X_TOLERANCE)
                    ]
                except Exception as exc:  # noqa: BLE001 - isolate single table
                    logger.warning("Table parse failed on page %d: %s", page_number, exc)
                    continue
                if _is_real_table(cells):
                    entries.append({
                        "cells": cells,
                        "bbox": tuple(table.bbox),
                        "origin": (page.bbox[0], page.bbox[1]),
                    })
            if entries:
                result[page_number] = entries
    return result
