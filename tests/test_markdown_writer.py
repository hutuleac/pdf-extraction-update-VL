from extractor.markdown_writer import _render_table, write_markdown

MODEL = {
    "document": {"filename": "doc.pdf", "source_type": "pdf", "pages": 2,
                 "author": "", "title": "",
                 "has_images": False, "image_count": 0},
    "pages": [
        {"unit": 1, "unit_type": "page", "has_images": False, "image_count": 0,
         "content": [
             {"type": "text", "content": "Procedura de concedii"},
             {"type": "table", "content": [["Rol", "Actiune"], ["Manager", "Aproba"]]},
         ]},
        {"unit": 2, "unit_type": "page", "has_images": False, "image_count": 0,
         "content": [{"type": "text", "content": "A doua pagina"}]},
    ],
}


def test_render_table_gfm():
    md = _render_table([["Rol", "Actiune"], ["Manager", "Aproba"]])
    lines = md.splitlines()
    assert lines[0] == "| Rol | Actiune |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| Manager | Aproba |"


def test_pipe_in_body_cell_does_not_invent_a_column():
    """A raw '|' would split one cell into two against a fixed-width header."""
    md = _render_table([["col", "desc"], ["a|b", "x"]])
    assert md.splitlines()[2] == r"| a\|b | x |"


def test_pipe_in_header_cell_is_escaped():
    md = _render_table([["a|b", "desc"]])
    assert md.splitlines()[0] == r"| a\|b | desc |"


def test_newline_in_cell_does_not_end_the_row():
    """A raw newline ends the table row and orphans the rest of the cell."""
    md = _render_table([["col", "desc"], ["x", "line1\nline2"]])
    lines = md.splitlines()
    assert len(lines) == 3
    assert lines[2] == "| x | line1<br>line2 |"


def test_crlf_in_cell_becomes_one_break():
    md = _render_table([["col"], ["line1\r\nline2"]])
    assert md.splitlines()[2] == "| line1<br>line2 |"


def test_backslash_in_cell_is_left_alone():
    """Escaping backslashes would rewrite every path, regex and LaTeX fragment."""
    md = _render_table([["path"], [r"C:\Users\report"]])
    assert md.splitlines()[2] == r"| C:\Users\report |"


def test_write_markdown_structure(tmp_path):
    out = write_markdown(MODEL, tmp_path)
    assert out.name == "doc.md"
    text = out.read_text(encoding="utf-8")
    assert "# doc.pdf" in text
    assert "## Page 1" in text
    assert "## Page 2" in text
    assert "| Rol | Actiune |" in text
    assert "---" in text  # page separator
    assert "Procedura de concedii" in text


def test_write_markdown_renders_image_block(tmp_path):
    model = {
        "document": {"filename": "img.pdf", "source_type": "pdf", "pages": 1,
                     "author": "", "title": "",
                     "has_images": True, "image_count": 1},
        "pages": [
            {"unit": 1, "unit_type": "page", "has_images": True, "image_count": 1,
             "content": [
                 {"type": "image", "path": "img_images/p001_i01.png", "width": 200, "height": 150},
             ]},
        ],
    }
    text = write_markdown(model, tmp_path).read_text(encoding="utf-8")
    assert "![](img_images/p001_i01.png)" in text


def test_write_markdown_unit_type_headings(tmp_path):
    model = {
        "document": {"filename": "d.xlsx", "source_type": "xlsx", "pages": 1,
                     "author": "", "title": "",
                     "has_images": False, "image_count": 0},
        "pages": [
            {"unit": 1, "unit_type": "sheet", "has_images": False,
             "image_count": 0, "content": [{"type": "text", "content": "x"}]},
        ],
    }
    text = write_markdown(model, tmp_path).read_text(encoding="utf-8")
    assert "## Sheet 1" in text
