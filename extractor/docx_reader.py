"""Word (.docx) extraction via python-docx.

The document body is walked in order: runs of consecutive paragraphs collapse
into one text block, each table becomes a table block. Everything lands in a
single `section` unit (Word has no page structure at the XML level).
"""
import logging
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from extractor.model import make_document, make_table_block, make_text_block, make_unit

logger = logging.getLogger(__name__)


def _table_rows(table: Table) -> list[list[str]]:
    """Return a table's cell text as rows of strings."""
    return [[cell.text for cell in row.cells] for row in table.rows]


def _flush_paragraphs(buffer: list[str], blocks: list[dict]) -> None:
    """Turn accumulated paragraph text into a single text block, if non-empty."""
    if not buffer:
        return
    text_block = make_text_block("\n".join(buffer))
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
            paragraph_buffer.append(Paragraph(child, document).text)
        elif child.tag == qn("w:tbl"):
            _flush_paragraphs(paragraph_buffer, blocks)
            blocks.append(make_table_block(_table_rows(Table(child, document))))
    _flush_paragraphs(paragraph_buffer, blocks)

    props = document.core_properties
    author = props.author or ""
    title = props.title or ""

    unit = make_unit(1, "section", blocks)
    return make_document(path.name, "docx", 1, [unit], author=author, title=title)
