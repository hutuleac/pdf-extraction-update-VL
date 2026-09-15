from extractor.model import (
    make_document,
    make_image_block,
    make_table_block,
    make_text_block,
    make_unit,
)


def test_make_text_block_normalizes():
    block = make_text_block("Manager     aproba")
    assert block == {"type": "text", "content": "Manager aproba"}


def test_make_text_block_empty_returns_none():
    assert make_text_block("   ") is None
    assert make_text_block("") is None


def test_make_table_block_shape():
    rows = [["Rol", "Actiune"], ["Manager", "Aproba"]]
    assert make_table_block(rows) == {"type": "table", "content": rows}


def test_make_table_block_normalizes_cells():
    # Cells go through the same normalization as any other text block —
    # collapsed whitespace here, and (per test_normalizer.py) Private Use
    # Area glyphs and cedilla diacritics elsewhere.
    rows = [["Rol", "Actiune"], ["Manager   ", "  Aproba"]]
    assert make_table_block(rows) == {
        "type": "table",
        "content": [["Rol", "Actiune"], ["Manager", "Aproba"]],
    }


def test_make_image_block_shape():
    block = make_image_block("stem_images/p001_i01.png", width=200, height=150)
    assert block == {
        "type": "image", "path": "stem_images/p001_i01.png", "width": 200, "height": 150,
    }


def test_make_unit_fields():
    blocks = [{"type": "text", "content": "hi"}]
    unit = make_unit(2, "sheet", blocks, image_count=3)
    assert unit == {
        "unit": 2,
        "unit_type": "sheet",
        "has_images": True,
        "image_count": 3,
        "content": blocks,
    }


def test_make_unit_defaults_no_images():
    unit = make_unit(1, "section", [])
    assert unit["has_images"] is False
    assert unit["image_count"] == 0


def test_make_document_fields():
    units = [make_unit(1, "page", [])]
    doc = make_document(
        "raport.pdf", "pdf", 1, units,
        author="Ana", title="Raport", image_count=4,
    )
    assert doc["document"] == {
        "filename": "raport.pdf",
        "source_type": "pdf",
        "pages": 1,
        "author": "Ana",
        "title": "Raport",
        "has_images": True,
        "image_count": 4,
        "schema_version": "2.0",
    }
    assert doc["pages"] == units


def test_make_document_defaults():
    doc = make_document("d.csv", "csv", 1, [])
    assert doc["document"]["author"] == ""
    assert doc["document"]["title"] == ""
    assert doc["document"]["has_images"] is False
    assert doc["document"]["image_count"] == 0
