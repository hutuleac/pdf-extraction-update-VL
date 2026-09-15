"""CSV extraction via the stdlib csv module.

One CSV file becomes a single `section` unit holding one table block. The
delimiter is sniffed (comma fallback). No pages, no images.

Encoding is detected via charset-normalizer instead of forcing UTF-8, so
cp1252 / Latin-1 files with diacritics decode correctly.
"""
import csv
import io
import logging
from pathlib import Path

from extractor.model import make_document, make_table_block, make_unit
from extractor.text_loader import load_text_file

logger = logging.getLogger(__name__)

_SNIFF_SAMPLE_BYTES = 4096


def _detect_delimiter(sample: str) -> str:
    """Sniff the delimiter from a text sample; fall back to comma."""
    if not sample.strip():
        return ","
    try:
        return csv.Sniffer().sniff(sample).delimiter
    except csv.Error:
        return ","


def extract_csv(path: Path | str) -> dict:
    """Extract a CSV file into the internal model (one section, one table)."""
    path = Path(path)

    # Encoding-aware loading (replaces hardcoded utf-8)
    load_result = load_text_file(path)
    text = load_result.text
    delimiter = _detect_delimiter(text[:_SNIFF_SAMPLE_BYTES])

    # csv.reader must own the record splitting. Feeding it text.splitlines()
    # breaks a quoted field at its internal newline, and the reader then
    # rejoins the halves without it — "line one\nline two" became
    # "line oneline two", a token in no source document.
    # newline=None (not the "" csv usually wants for real files) folds CRLF to
    # LF inside the buffer, so a CRLF source yields the same cell text as a LF
    # one instead of leaking a stray \r into the corpus.
    rows = [
        list(row)
        for row in csv.reader(io.StringIO(text, newline=None), delimiter=delimiter)
    ]
    blocks = [make_table_block(rows)] if rows else []
    unit = make_unit(1, "section", blocks)

    # Propagate encoding warnings to the document model
    warnings = load_result.warnings or None
    return make_document(path.name, "csv", 1, [unit], warnings=warnings)
