"""Internal data model constructors — the single source of the model shape.

Every reader (PDF, CSV, Word, Excel) builds its output through these helpers so
the block/unit/document shape is defined in exactly one place. Both writers
(JSON, Markdown) consume that shape.
"""
from extractor.errors import ResourceLimitError
from extractor.limits import MAX_TABLE_CELLS
from extractor.normalizer import normalize

SCHEMA_VERSION = "2.0"


def make_text_block(raw_text: str, *, preserve_layout: bool = False) -> dict | None:
    """Normalize raw text into a text block, or None if empty after cleaning.

    *preserve_layout* is for authored Markdown, where indentation is
    structure — see ``normalizer.normalize``.
    """
    normalized = normalize(raw_text, preserve_layout=preserve_layout)
    if not normalized:
        return None
    return {"type": "text", "content": normalized}


def make_heading_block(raw_text: str, level: int) -> dict | None:
    """A section heading the source marks as one (a Word heading style).

    Its own block type rather than a ``#`` prefix inside a text block, so the
    JSON stays structural and the Markdown writer decides the rendering.
    *level* is the source's own level, 1-based; the writer nests it under the
    unit heading.
    """
    normalized = normalize(raw_text)
    if not normalized:
        return None
    return {"type": "heading", "level": max(1, int(level)), "content": normalized}


def make_ocr_text_block(raw_text: str, confidence: float) -> dict | None:
    """Normalize OCR output into a text block that records where it came from.

    OCR text reuses the ``text`` type on purpose, so it flows through the
    Markdown writer, the metrics and any later chunking exactly like native
    text. ``source`` and ``confidence`` are present only on OCR blocks, which
    keeps every other block byte-identical to before.
    """
    normalized = normalize(raw_text)
    if not normalized:
        return None
    return {
        "type": "text",
        "content": normalized,
        "source": "ocr",
        "confidence": round(float(confidence), 3),
    }


def make_vlm_text_block(raw_text: str, source: str = "vlm") -> dict | None:
    """Normalize visual-model output into a text block that records its source.

    Same reasoning as ``make_ocr_text_block``: the ``text`` type is reused so
    the content flows through Markdown, metrics and later chunking like any
    other text. There is no confidence — the model reports none; its output is
    accepted or rejected wholesale in ``vlm/apply.py``.
    """
    normalized = normalize(raw_text)
    if not normalized:
        return None
    return {"type": "text", "content": normalized, "source": source}


def make_header_block(raw_text: str) -> dict | None:
    """Normalize raw text into a header block, or None if empty after cleaning.

    Header blocks represent repeated page headers detected by the pipeline.
    They appear in JSON but are excluded from Markdown output.
    """
    normalized = normalize(raw_text)
    if not normalized:
        return None
    return {"type": "header", "content": normalized}


def make_footer_block(raw_text: str) -> dict | None:
    """Normalize raw text into a footer block, or None if empty after cleaning.

    Footer blocks represent repeated page footers detected by the pipeline.
    They appear in JSON but are excluded from Markdown output.
    """
    normalized = normalize(raw_text)
    if not normalized:
        return None
    return {"type": "footer", "content": normalized}


def make_table_block(rows: list[list[str]], *, source: str = "native") -> dict:
    """Wrap table rows (list of lists of cell strings) in a table block.

    Cells go through the same normalization as every other text block —
    pdfplumber reads them raw, so without this a table is the one place in
    the document where PUA glyphs, cedilla diacritics, and encoding mojibake
    would reach the output untouched.

    *source* defaults to ``"native"`` (pdfplumber ruling-line extraction, or
    the equivalent structured read for non-PDF formats). PDF's visual-model
    fallback passes ``"vlm"`` for tables it inferred on a page pdfplumber
    found none on — those are a model's guess, not a verified read, and
    without this field they were indistinguishable from a native table.
    """
    cell_count = sum(len(row) for row in rows)
    if cell_count > MAX_TABLE_CELLS:
        raise ResourceLimitError("table cells", cell_count, MAX_TABLE_CELLS)
    normalized_rows = [[normalize(cell) for cell in row] for row in rows]
    block = {"type": "table", "content": normalized_rows}
    if source != "native":
        block["source"] = source
    return block


def make_image_block(path: str, *, width: int, height: int) -> dict:
    """Wrap a saved image's relative path (from the output file's directory)
    in an image block, so the Markdown writer can embed it and the JSON keeps
    a record of what was extracted."""
    return {"type": "image", "path": path, "width": width, "height": height}


def make_unit(number: int, unit_type: str, blocks: list[dict],
              image_count: int = 0, *, page_class: str | None = None,
              title: str | None = None) -> dict:
    """Build one logical unit (page / section / sheet) from its content blocks.

    The optional *page_class* (e.g. 'native-text', 'scanned', 'mixed',
    'garbled', 'layout-complex') is included only when provided, keeping
    existing callers unchanged. *title* is the unit's own name where the
    source has one (a worksheet tab); the Markdown writer puts it in the
    unit heading, because "Sheet 2" says nothing and "Sheet 2: Budget" does.
    """
    unit: dict = {
        "unit": number,
        "unit_type": unit_type,
        "has_images": image_count > 0,
        "image_count": image_count,
        "content": blocks,
    }
    if page_class is not None:
        unit["page_class"] = page_class
    if title:
        unit["title"] = title
    return unit


def make_document(filename: str, source_type: str, unit_count: int,
                  units: list[dict], *, author: str = "", title: str = "",
                  image_count: int = 0,
                  warnings: list[dict] | None = None) -> dict:
    """Assemble the full internal model from its units and document metadata.

    *warnings* is an optional list of dicts like ``{"code": "...", "page": N}``.
    When absent or empty the key is omitted from output, keeping existing
    behaviour byte-identical.
    """
    doc: dict = {
        "filename": filename,
        "source_type": source_type,
        "pages": unit_count,
        "author": author,
        "title": title,
        "has_images": image_count > 0,
        "image_count": image_count,
        "schema_version": SCHEMA_VERSION,
    }
    if warnings:
        doc["warnings"] = warnings

    return {
        "document": doc,
        "pages": units,
    }
