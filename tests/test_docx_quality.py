from docx import Document
from docx.oxml import OxmlElement

from extractor.docx_reader import extract_docx


def test_docx_preserves_hyperlink_display_text_and_body_order(tmp_path):
    path = tmp_path / "structured.docx"
    document = Document()
    document.add_paragraph("Project heading", style="Heading 1")
    document.add_paragraph("First list item", style="List Number")
    document.add_paragraph("Second list item", style="List Bullet 2")
    paragraph = document.add_paragraph()
    hyperlink = OxmlElement("w:hyperlink")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Visible link"
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Cell"
    document.add_paragraph("After table")
    document.save(path)

    blocks = extract_docx(path)["pages"][0]["content"]
    assert [block["type"] for block in blocks] == ["heading", "text", "table", "text"]
    assert blocks[0] == {"type": "heading", "level": 1, "content": "Project heading"}
    # List paragraphs keep a marker and paragraphs are separated by a blank
    # line, so the Markdown keeps the structure Word had.
    assert blocks[1]["content"] == "1. First list item\n- Second list item\n\nVisible link"
    assert blocks[2]["content"] == [["Cell"]]
    assert blocks[3]["content"] == "After table"


def test_docx_merged_cells_are_not_repeated(tmp_path):
    """python-docx returns the same cell once per spanned column."""
    path = tmp_path / "merged.docx"
    document = Document()
    table = document.add_table(rows=2, cols=3)
    merged = table.cell(0, 0).merge(table.cell(0, 2))
    merged.text = "Wide header"
    table.cell(1, 0).text, table.cell(1, 1).text, table.cell(1, 2).text = "a", "b", "c"
    document.save(path)

    rows = extract_docx(path)["pages"][0]["content"][0]["content"]
    assert rows == [["Wide header", "", ""], ["a", "b", "c"]]
