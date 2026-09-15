"""Tests for the new format readers: txt, md, html, pptx, json, xml.

Covers happy path, edge cases, and error handling per python-testing patterns.
Uses parametrize where multiple inputs share the same assertion pattern.
"""
import json

import pytest

from extractor.html_reader import extract_html
from extractor.json_reader import extract_json
from extractor.md_reader import extract_md
from extractor.pptx_reader import extract_pptx
from extractor.txt_reader import extract_txt
from extractor.xml_reader import extract_xml

# ---------------------------------------------------------------------------
# TXT reader
# ---------------------------------------------------------------------------

class TestTxtReader:
    def test_basic_extraction(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello world\nSecond line", encoding="utf-8")
        model = extract_txt(f)
        assert model["document"]["source_type"] == "txt"
        assert model["document"]["filename"] == "hello.txt"
        assert model["document"]["pages"] == 1
        blocks = model["pages"][0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "Hello world" in blocks[0]["content"]

    def test_empty_file_produces_no_blocks(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        model = extract_txt(f)
        assert model["pages"][0]["content"] == []

    def test_non_utf8_encoding(self, tmp_path):
        f = tmp_path / "latin.txt"
        # Use cp1250 which supports Romanian ă, î, etc.
        f.write_bytes("Înregistrare completă".encode("cp1250"))
        model = extract_txt(f)
        assert "\ufffd" not in model["pages"][0]["content"][0]["content"]


# ---------------------------------------------------------------------------
# MD reader
# ---------------------------------------------------------------------------

class TestMdReader:
    def test_basic_extraction(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Title\n\nSome paragraph.", encoding="utf-8")
        model = extract_md(f)
        assert model["document"]["source_type"] == "md"
        assert model["document"]["filename"] == "readme.md"
        blocks = model["pages"][0]["content"]
        assert len(blocks) == 1
        assert "# Title" in blocks[0]["content"]

    def test_markdown_source_preserved_faithfully(self, tmp_path):
        """Markdown is pass-through — structure markers are preserved."""
        source = "# Heading\n\n```python\ncode()\n```\n\nParagraph text."
        f = tmp_path / "doc.md"
        f.write_text(source, encoding="utf-8")
        model = extract_md(f)
        content = model["pages"][0]["content"][0]["content"]
        assert "# Heading" in content
        assert "```python" in content
        assert "Paragraph text." in content


# ---------------------------------------------------------------------------
# HTML reader
# ---------------------------------------------------------------------------

class TestHtmlReader:
    def test_extracts_paragraphs_as_text_blocks(self, tmp_path):
        html = "<html><body><p>First para</p><p>Second para</p></body></html>"
        f = tmp_path / "test.html"
        f.write_text(html, encoding="utf-8")
        model = extract_html(f)
        assert model["document"]["source_type"] == "html"
        blocks = model["pages"][0]["content"]
        text_blocks = [b for b in blocks if b["type"] == "text"]
        assert len(text_blocks) >= 2
        assert "First para" in text_blocks[0]["content"]

    def test_extracts_table_as_table_block(self, tmp_path):
        html = """<html><body>
        <table><tr><th>Name</th><th>Age</th></tr>
        <tr><td>Alice</td><td>30</td></tr></table>
        </body></html>"""
        f = tmp_path / "table.html"
        f.write_text(html, encoding="utf-8")
        model = extract_html(f)
        blocks = model["pages"][0]["content"]
        tables = [b for b in blocks if b["type"] == "table"]
        assert len(tables) == 1
        assert tables[0]["content"][0] == ["Name", "Age"]
        assert tables[0]["content"][1] == ["Alice", "30"]

    def test_strips_script_and_style_tags(self, tmp_path):
        html = """<html><body>
        <script>alert('xss')</script>
        <style>.x{color:red}</style>
        <p>Visible text</p>
        </body></html>"""
        f = tmp_path / "script.html"
        f.write_text(html, encoding="utf-8")
        model = extract_html(f)
        all_text = " ".join(
            b["content"] for b in model["pages"][0]["content"] if b["type"] == "text"
        )
        assert "alert" not in all_text
        assert "color:red" not in all_text
        assert "Visible text" in all_text

    def test_ragged_table_rows_padded(self, tmp_path):
        html = """<html><body><table>
        <tr><td>A</td><td>B</td><td>C</td></tr>
        <tr><td>1</td><td>2</td></tr>
        </table></body></html>"""
        f = tmp_path / "ragged.html"
        f.write_text(html, encoding="utf-8")
        model = extract_html(f)
        tables = [b for b in model["pages"][0]["content"] if b["type"] == "table"]
        assert len(tables[0]["content"][1]) == 3  # padded to 3 columns

    def test_nested_text_tags_are_not_duplicated(self, tmp_path):
        # AUDIT 4: <li><p> and <blockquote><p> used to emit their text twice.
        html = (
            "<html><body><ul><li><p>ITEM ONE</p></li></ul>"
            "<blockquote><p>QUOTED</p></blockquote></body></html>"
        )
        f = tmp_path / "nested.html"
        f.write_text(html, encoding="utf-8")
        model = extract_html(f)
        texts = [b["content"] for b in model["pages"][0]["content"] if b["type"] == "text"]
        assert texts.count("ITEM ONE") == 1
        assert texts.count("QUOTED") == 1

    def test_nested_inline_children_are_kept(self, tmp_path):
        # A container's own inline children (not matched by _TEXT_TAGS) must
        # still surface, even though the container also holds a nested block.
        html = "<html><body><li><b>Intro</b><p>body</p></li></body></html>"
        f = tmp_path / "inline.html"
        f.write_text(html, encoding="utf-8")
        model = extract_html(f)
        texts = [b["content"] for b in model["pages"][0]["content"] if b["type"] == "text"]
        assert any("Intro" in t for t in texts)
        assert any("body" in t for t in texts)


# ---------------------------------------------------------------------------
# PPTX reader
# ---------------------------------------------------------------------------

class TestPptxReader:
    @pytest.fixture
    def sample_pptx(self, tmp_path):
        """A minimal 2-slide deck with text and a table."""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        # Slide 1: text
        slide1 = prs.slides.add_slide(prs.slide_layouts[5])  # blank
        txBox = slide1.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
        txBox.text_frame.text = "Slide one content"
        # Slide 2: table
        slide2 = prs.slides.add_slide(prs.slide_layouts[5])
        table_shape = slide2.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(2))
        tbl = table_shape.table
        tbl.cell(0, 0).text = "Header1"
        tbl.cell(0, 1).text = "Header2"
        tbl.cell(1, 0).text = "Val1"
        tbl.cell(1, 1).text = "Val2"
        out = tmp_path / "deck.pptx"
        prs.save(out)
        return out

    def test_slide_unit_type(self, sample_pptx):
        model = extract_pptx(sample_pptx)
        assert model["document"]["source_type"] == "pptx"
        assert model["document"]["pages"] == 2
        for unit in model["pages"]:
            assert unit["unit_type"] == "slide"

    def test_text_extracted_from_text_frames(self, sample_pptx):
        model = extract_pptx(sample_pptx)
        blocks = model["pages"][0]["content"]
        text_blocks = [b for b in blocks if b["type"] == "text"]
        assert any("Slide one content" in b["content"] for b in text_blocks)

    def test_table_extracted_from_graphic_frame(self, sample_pptx):
        model = extract_pptx(sample_pptx)
        blocks = model["pages"][1]["content"]
        tables = [b for b in blocks if b["type"] == "table"]
        assert len(tables) == 1
        assert tables[0]["content"][0] == ["Header1", "Header2"]
        assert tables[0]["content"][1] == ["Val1", "Val2"]

    # --- AUDIT 13: grouped shapes ------------------------------------------

    @pytest.fixture
    def grouped_pptx(self, tmp_path):
        """A deck whose text lives inside a group shape, as real decks do."""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        one = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
        one.text_frame.text = "Inside the group"
        two = slide.shapes.add_textbox(Inches(4), Inches(1), Inches(2), Inches(1))
        two.text_frame.text = "Also grouped"
        slide.shapes.add_group_shape([one, two])

        out = tmp_path / "grouped.pptx"
        prs.save(out)
        return out

    def test_text_inside_a_group_shape_is_extracted(self, grouped_pptx):
        """Grouping is standard practice; its text was silently dropped."""
        model = extract_pptx(grouped_pptx)
        text = " ".join(
            b["content"] for b in model["pages"][0]["content"] if b["type"] == "text"
        )
        assert "Inside the group" in text
        assert "Also grouped" in text

    # --- AUDIT 14: pictures on a slide ---------------------------------------

    @pytest.fixture
    def picture_pptx(self, tmp_path, scanned_image):
        """A slide whose only content is a picture of text."""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.add_picture(str(scanned_image), Inches(1), Inches(1))

        out = tmp_path / "picture.pptx"
        prs.save(out)
        return out

    def test_slide_picture_is_counted(self, picture_pptx):
        model = extract_pptx(picture_pptx)
        assert model["pages"][0]["image_count"] == 1
        assert model["document"]["image_count"] == 1

    def test_picture_only_slide_explains_itself(self, picture_pptx, monkeypatch):
        """Without OCR the slide must say why it is empty, not just be empty."""
        from extractor.ocr import registry
        from extractor.ocr.base import OcrUnavailable, UnavailableReason

        failure = OcrUnavailable(UnavailableReason.DISABLED, "ocr disabled")
        monkeypatch.setattr(registry, "is_available", lambda: False)
        monkeypatch.setattr(registry, "unavailable_reason", lambda: failure)

        model = extract_pptx(picture_pptx)
        codes = [w["code"] for w in model["document"].get("warnings", [])]
        assert "OCR_SKIPPED_DISABLED" in codes

    def test_slide_without_pictures_emits_no_ocr_warning(self, sample_pptx):
        """Nothing to read is not a problem worth a warning."""
        model = extract_pptx(sample_pptx)
        codes = [w["code"] for w in model["document"].get("warnings", [])]
        assert not any(code.startswith("OCR_") for code in codes)


# ---------------------------------------------------------------------------
# JSON reader
# ---------------------------------------------------------------------------

class TestJsonReader:
    def test_flat_records_become_table(self, tmp_path):
        data = [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]
        f = tmp_path / "records.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        model = extract_json(f)
        assert model["document"]["source_type"] == "json"
        blocks = model["pages"][0]["content"]
        tables = [b for b in blocks if b["type"] == "table"]
        assert len(tables) == 1
        assert tables[0]["content"][0] == ["name", "age"]
        assert tables[0]["content"][1] == ["Alice", "30"]

    def test_nested_json_becomes_text(self, tmp_path):
        data = {"config": {"debug": True, "items": [1, 2, 3]}}
        f = tmp_path / "nested.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        model = extract_json(f)
        blocks = model["pages"][0]["content"]
        text_blocks = [b for b in blocks if b["type"] == "text"]
        assert len(text_blocks) == 1
        assert "config" in text_blocks[0]["content"]

    def test_malformed_json_raises_value_error(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            extract_json(f)

    def test_empty_array_produces_no_table(self, tmp_path):
        f = tmp_path / "empty_arr.json"
        f.write_text("[]", encoding="utf-8")
        model = extract_json(f)
        # Empty list is not flat records, goes through nested path
        # Result is an empty text block (no content after normalization)
        blocks = model["pages"][0]["content"]
        assert blocks == []


# ---------------------------------------------------------------------------
# XML reader
# ---------------------------------------------------------------------------

class TestXmlReader:
    def test_basic_xml_extraction(self, tmp_path):
        xml = "<root><item>Hello</item><item>World</item></root>"
        f = tmp_path / "test.xml"
        f.write_text(xml, encoding="utf-8")
        model = extract_xml(f)
        assert model["document"]["source_type"] == "xml"
        blocks = model["pages"][0]["content"]
        text_blocks = [b for b in blocks if b["type"] == "text"]
        assert len(text_blocks) == 1
        assert "Hello" in text_blocks[0]["content"]
        assert "World" in text_blocks[0]["content"]

    def test_malformed_xml_raises_value_error(self, tmp_path):
        f = tmp_path / "bad.xml"
        f.write_text("<unclosed>", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid XML"):
            extract_xml(f)

    def test_xxe_attack_refused(self, tmp_path):
        xxe = """<?xml version="1.0"?>
        <!DOCTYPE foo [
          <!ENTITY xxe SYSTEM "file:///etc/passwd">
        ]>
        <root>&xxe;</root>"""
        f = tmp_path / "xxe.xml"
        f.write_text(xxe, encoding="utf-8")
        with pytest.raises(ValueError, match=r"Refused XML|Invalid XML"):
            extract_xml(f)

    def test_attributes_rendered(self, tmp_path):
        xml = '<root><item id="1" name="test">Content</item></root>'
        f = tmp_path / "attrs.xml"
        f.write_text(xml, encoding="utf-8")
        model = extract_xml(f)
        content = model["pages"][0]["content"][0]["content"]
        assert "id" in content
        assert "Content" in content


# ---------------------------------------------------------------------------
# Parametrized: all readers produce correct source_type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext,content,expected_type", [
    (".txt", "plain text", "txt"),
    (".md", "# Heading", "md"),
    (".html", "<html><body><p>hi</p></body></html>", "html"),
    (".json", '{"key": "val"}', "json"),
    (".xml", "<root><item>x</item></root>", "xml"),
], ids=["txt", "md", "html", "json", "xml"])
def test_reader_source_type(tmp_path, ext, content, expected_type):
    """Each reader sets the correct source_type on the document model."""
    from extractor.dispatcher import extract_document

    f = tmp_path / f"test{ext}"
    f.write_text(content, encoding="utf-8")
    model = extract_document(f)
    assert model["document"]["source_type"] == expected_type
    assert model["document"]["pages"] >= 1
