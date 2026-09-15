import json

from main import discover_inputs, file_stats, format_summary, process_file


def test_file_stats_counts():
    model = {
        "document": {"filename": "d.pdf", "source_type": "pdf", "pages": 2,
                     "image_count": 5},
        "pages": [
            {"content": [{"type": "text", "content": "a"},
                         {"type": "table", "content": [["x"]]}]},
            {"content": [{"type": "text", "content": "b"}]},
        ],
    }
    stats = file_stats(model)
    assert stats == {
        "filename": "d.pdf",
        "source_type": "pdf",
        "units": 2,
        "text_blocks": 2,
        "table_blocks": 1,
        "image_count": 5,
        "ocr_pages": 0,
        "vlm_pages": 0,
        "vlm_formulas": 0,
        "vlm_figures": 0,
        "unreadable_pages": 0,
        "warnings": [],
        "ok": True,
    }


def test_format_summary_totals_and_stop():
    results = [
        {"filename": "a.pdf", "source_type": "pdf", "units": 3,
         "text_blocks": 3, "table_blocks": 1, "image_count": 4, "ok": True},
        {"filename": "b.xlsx", "source_type": "xlsx", "units": 1,
         "text_blocks": 0, "table_blocks": 1, "image_count": 0, "ok": True},
        {"filename": "c.docx", "ok": False, "error": "boom"},
    ]
    lines = format_summary(results)
    text = "\n".join(lines)
    assert "Run complete" in text
    assert "3 file(s): 2 succeeded, 1 failed" in text
    assert "4 units" in text          # 3 + 1
    assert "2 table block(s)" in text  # 1 + 1
    assert "4 image(s)" in text
    assert "a.pdf" in text and "b.xlsx" in text
    assert "c.docx" in text and "FAILED" in text


def test_process_pdf_end_to_end(sample_pdf, tmp_path):
    json_dir = tmp_path / "json"
    md_dir = tmp_path / "md"
    json_path, md_path, model = process_file(sample_pdf, json_dir, md_dir)

    assert json_path.exists()
    assert md_path.exists()
    assert model["document"]["source_type"] == "pdf"

    model = json.loads(json_path.read_text(encoding="utf-8"))
    assert model["document"]["filename"] == "procedura_test.pdf"
    assert model["document"]["source_type"] == "pdf"
    assert model["document"]["pages"] == 2
    assert len(model["pages"]) == 2
    unit = model["pages"][0]
    assert unit["unit"] == 1
    assert unit["unit_type"] == "page"
    assert any(b["type"] == "text" for b in unit["content"])

    md = md_path.read_text(encoding="utf-8")
    assert "# procedura_test.pdf" in md
    assert "## Page 1" in md


def test_process_csv_end_to_end(sample_csv, tmp_path):
    json_path, md_path, _model = process_file(sample_csv, tmp_path / "json", tmp_path / "md")
    model = json.loads(json_path.read_text(encoding="utf-8"))
    assert model["document"]["source_type"] == "csv"
    assert model["pages"][0]["unit_type"] == "section"
    assert "## Section 1" in md_path.read_text(encoding="utf-8")


def test_table_content_not_duplicated_in_text(table_pdf, tmp_path):
    """Table cell text must appear only in the table block, not the text block."""
    json_path, _md, _model = process_file(table_pdf, tmp_path / "json", tmp_path / "md")
    model = json.loads(json_path.read_text(encoding="utf-8"))
    blocks = model["pages"][0]["content"]

    text_blocks = [b["content"] for b in blocks if b["type"] == "text"]
    table_blocks = [b for b in blocks if b["type"] == "table"]
    assert table_blocks, "expected a table block"

    joined_text = " ".join(text_blocks)
    # A cell value unique to the table must not leak into the text block.
    assert "Inregistreaza" not in joined_text
    assert "Inregistreaza" in str(table_blocks[0]["content"])
    # The paragraph text is still present.
    assert "Procedura de concedii" in joined_text


def test_discover_inputs_filters_by_extension(tmp_path):
    (tmp_path / "a.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "b.csv").write_text("x", encoding="utf-8")
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    (tmp_path / "d.docx").write_text("x", encoding="utf-8")
    found = {p.name for p in discover_inputs(tmp_path)}
    assert found == {"a.pdf", "b.csv", "c.txt", "d.docx"}
