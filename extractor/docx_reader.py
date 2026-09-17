"""Word (.docx) extraction via python-docx.

The document body is walked in order: a heading-styled paragraph becomes a
heading block, runs of consecutive body paragraphs collapse into one text
block (paragraphs separated by a blank line, list items prefixed with a
marker), each table becomes a table block. Everything lands in a single
`section` unit (Word has no page structure at the XML level).
"""
import logging
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from extractor.model import (
    make_document,
    make_heading_block,
    make_table_block,
    make_text_block,
    make_unit,
)

logger = logging.getLogger(__name__)

_HEADING_STYLE = re.compile(r"^heading (\d)$", re.IGNORECASE)


def _heading_level(paragraph: Paragraph) -> int | None:
    """The heading level a paragraph's style declares, or None for body text."""
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name.lower() == "title":
        return 1
    match = _HEADING_STYLE.match(name)
    return int(match.group(1)) if match else None


def _list_marker(paragraph: Paragraph) -> str:
    """A Markdown list marker for list-styled paragraphs, else ''.

    The style name is the signal ("List Bullet", "List Number 2"). The
    numbering itself lives in numbering.xml and is not resolved: "1." on every
    numbered item is valid Markdown and renders as a counted list.
    """
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    lowered = name.lower()
    if lowered.startswith("list number"):
        return "1. "
    if lowered.startswith("list"):
        return "- "
    return ""


def _is_list_item(text: str) -> bool:
    return text.startswith(("- ", "1. "))


def _table_rows(table: Table) -> list[list[str]]:
    """Return a table's cell text as rows of strings.

    python-docx repeats one cell object across a horizontal merge, so the
    merged text would otherwise appear once per spanned column.
    """
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            cells.append("" if cell._tc is previous else cell.text)
            previous = cell._tc
        rows.append(cells)
    return rows


def _flush_paragraphs(buffer: list[str], blocks: list[dict]) -> None:
    """Turn accumulated paragraph text into a single text block, if non-empty."""
    if not buffer:
        return
    # Consecutive list items stay one tight list; everything else is a paragraph.
    parts: list[str] = []
    for previous, current in zip([None, *buffer], buffer, strict=False):
        if previous is not None:
            parts.append("\n" if _is_list_item(previous) and _is_list_item(current) else "\n\n")
        parts.append(current)
    text_block = make_text_block("".join(parts))
    if text_block:
        blocks.append(text_block)
    buffer.clear()


def extract_docx(path: Path | str) -> dict:
    """Extract a Word document into the internal model (one section unit)."""
    path = Path(path)
    document = Document(path)
    body = document.element.body

    blocks: list[dict] = []
    paragraph_buffer: list[str] = []
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            level = _heading_level(paragraph)
            if level is not None:
                _flush_paragraphs(paragraph_buffer, blocks)
                heading = make_heading_block(paragraph.text, level)
                if heading:
                    blocks.append(heading)
                continue
            text = paragraph.text.strip()
            if text:
                paragraph_buffer.append(_list_marker(paragraph) + text)
        elif child.tag == qn("w:tbl"):
            _flush_paragraphs(paragraph_buffer, blocks)
            blocks.append(make_table_block(_table_rows(Table(child, document))))
    _flush_paragraphs(paragraph_buffer, blocks)

    props = document.core_properties
    author = props.author or ""
    title = props.title or ""

    unit = make_unit(1, "section", blocks)
    return make_document(path.name, "docx", 1, [unit], author=author, title=title)
