"""Markdown (.md) extraction using encoding-aware loading.

Markdown source is passed through rather than parsed into structure, so
authored Markdown stays faithful. One file becomes a single `section` unit
holding one text block with the raw source content.
"""
from pathlib import Path

from extractor.model import make_document, make_text_block, make_unit
from extractor.text_loader import load_text_file


def extract_md(path: Path | str) -> dict:
    """Extract a Markdown file into the internal model (one section, one text block)."""
    path = Path(path)
    load_result = load_text_file(path)

    blocks: list[dict] = []
    # Layout is the structure here: indentation nests lists, blank lines end
    # paragraphs, fences hold code. The default normalization rewrote all of it.
    text_block = make_text_block(load_result.text, preserve_layout=True)
    if text_block:
        blocks.append(text_block)

    unit = make_unit(1, "section", blocks)
    warnings = load_result.warnings or None
    return make_document(path.name, "md", 1, [unit], warnings=warnings)
