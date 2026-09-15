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
    assert [block["type"] for block in blocks] == ["text", "table", "text"]
    assert "Project heading" in blocks[0]["content"]
    assert "First list item" in blocks[0]["content"]
    assert "Visible link" in blocks[0]["content"]
    assert blocks[1]["content"] == [["Cell"]]
    assert blocks[2]["content"] == "After table"
