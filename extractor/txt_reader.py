"""Plain text (.txt) extraction using encoding-aware loading.

One text file becomes a single `section` unit holding one text block.
No pages, no images.
"""
from pathlib import Path

from extractor.model import make_document, make_text_block, make_unit
from extractor.text_loader import load_text_file


def extract_txt(path: Path | str) -> dict:
    """Extract a plain text file into the internal model (one section, one text block)."""
    path = Path(path)
    load_result = load_text_file(path)

    blocks: list[dict] = []
    text_block = make_text_block(load_result.text)
    if text_block:
        blocks.append(text_block)

    unit = make_unit(1, "section", blocks)
    warnings = load_result.warnings or None
    return make_document(path.name, "txt", 1, [unit], warnings=warnings)
