"""HTML extraction via BeautifulSoup.

Drops script, style, nav, and comments. Walks the body in document order
turning headings and paragraphs into text blocks and <table> elements into
table blocks (normalising ragged rows). One `section` unit.
"""
import logging
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from extractor.model import make_document, make_table_block, make_text_block, make_unit
from extractor.text_loader import load_text_file

logger = logging.getLogger(__name__)

# Tags to remove entirely (including their content)
_STRIP_TAGS = {"script", "style", "nav"}

# Tags that produce text blocks
_TEXT_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "dd", "dt"}


def _parse_table(table_element) -> list[list[str]]:
    """Parse an HTML table into rows of cell strings.

    Handles <thead>, <tbody>, <tfoot>, and bare <tr>. Ragged rows are padded
    to the maximum width.
    """
    rows: list[list[str]] = []
    for tr in table_element.find_all("tr", recursive=True):
        cells = []
        for td in tr.find_all(["td", "th"], recursive=False):
            cells.append(td.get_text(separator=" ", strip=True))
        if cells:
            rows.append(cells)

    # Normalise ragged rows to max width
    if rows:
        max_cols = max(len(r) for r in rows)
        rows = [(r + [""] * max_cols)[:max_cols] for r in rows]

    return rows


def _own_text(element) -> str:
    """Text belonging to this element itself, excluding nested text blocks.

    A container such as <li><p>...</p></li> matches _TEXT_TAGS itself and so
    does its child, so emitting both duplicates the content. Inline children
    (<b>, <span>, <a>) are NOT text blocks and must still be included, so this
    walks direct children rather than taking direct strings only.
    """
    if not element.find(list(_TEXT_TAGS)):
        return element.get_text(separator=" ", strip=True)

    parts: list[str] = []
    for child in element.children:
        name = getattr(child, "name", None)
        if name in _TEXT_TAGS:
            continue  # emitted on its own visit
        text = child.get_text(separator=" ", strip=True) if name else str(child).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def extract_html(path: Path | str) -> dict:
    """Extract an HTML file into the internal model (one section unit)."""
    path = Path(path)
    load_result = load_text_file(path)

    # Try lxml parser first (already a dependency), fall back to html.parser
    try:
        soup = BeautifulSoup(load_result.text, "lxml")
    except Exception:  # noqa: BLE001
        soup = BeautifulSoup(load_result.text, "html.parser")

    # Remove unwanted tags
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    # Remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    body = soup.body or soup

    blocks: list[dict] = []
    # Track tables we've already processed (to avoid nested-table duplication)
    processed_tables: set[int] = set()

    for element in body.descendants:
        if not hasattr(element, "name") or element.name is None:
            continue

        # Skip elements inside already-processed tables
        if any(
            id(parent) in processed_tables
            for parent in element.parents
            if hasattr(parent, "name") and parent.name == "table"
        ):
            continue

        if element.name == "table":
            processed_tables.add(id(element))
            rows = _parse_table(element)
            if rows:
                blocks.append(make_table_block(rows))

        elif element.name in _TEXT_TAGS:
            # Skip if inside a table (handled above)
            if element.find_parent("table"):
                continue
            text = _own_text(element)
            text_block = make_text_block(text)
            if text_block:
                blocks.append(text_block)

    # If no structured content found, fall back to body text
    if not blocks:
        full_text = body.get_text(separator="\n", strip=True)
        text_block = make_text_block(full_text)
        if text_block:
            blocks.append(text_block)

    unit = make_unit(1, "section", blocks)
    warnings = load_result.warnings or None
    return make_document(path.name, "html", 1, [unit], warnings=warnings)
