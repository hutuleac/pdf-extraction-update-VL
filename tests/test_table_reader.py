import pymupdf

from extractor.table_reader import _clean_row, _is_real_table, extract_tables


def test_no_tables_returns_empty(sample_pdf):
    assert extract_tables(sample_pdf) == {}


def _table_pdf_with_vertical_stamp(path, pages: int, stamp_word: str | None):
    """Same ruled 2x3 table as table_pdf, repeated over *pages* pages, with a
    vertical (rotate=90) word placed clear of the real cell text — the
    watermark spot: inside the table's bbox, so it reaches table_reader's
    char filter, but not overlapping any cell's characters."""
    doc = pymupdf.open()
    x0, y0, w, h, rows, cols = 72, 120, 200, 60, 3, 2
    cw, rh = w / cols, h / rows
    cells = [["Rol", "Actiune"], ["Manager", "Aproba"], ["HR", "Inregistreaza"]]
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), "Procedura de concedii.")
        for r in range(rows + 1):
            page.draw_line((x0, y0 + r * rh), (x0 + w, y0 + r * rh))
        for c in range(cols + 1):
            page.draw_line((x0 + c * cw, y0), (x0 + c * cw, y0 + h))
        for r in range(rows):
            for c in range(cols):
                page.insert_text((x0 + c * cw + 5, y0 + r * rh + 15), cells[r][c])
        if stamp_word:
            page.insert_text((x0 + 90, y0 + 18), stamp_word, fontsize=6, rotate=90)
    doc.save(path)
    doc.close()
    return path


def test_repeated_vertical_stamp_is_dropped_from_table_cells(tmp_path):
    path = _table_pdf_with_vertical_stamp(tmp_path / "stamped.pdf", 6, "ZZWATERMARK")

    result = extract_tables(path)

    for page in range(1, 7):
        cells = result[page][0]["cells"]
        assert "ZZWATERMARK" not in " ".join(cell for row in cells for cell in row)
        # The real content the stamp merely shares a table with must survive.
        assert cells == [["Rol", "Actiune"], ["Manager", "Aproba"], ["HR", "Inregistreaza"]]


def test_one_off_vertical_label_is_kept_in_table_cells(tmp_path):
    # Below repeated_vertical_boxes's min_pages, so it reads as a genuine
    # one-off rotated label (a real sideways note), not a stamp.
    path = _table_pdf_with_vertical_stamp(tmp_path / "unique.pdf", 1, "NOTE")

    result = extract_tables(path)

    cells = result[1][0]["cells"]
    # Vertical text authored bottom-to-top reads back reversed ("ETON"), same
    # as the real watermark did — repeated_vertical_boxes only needs it kept,
    # not correctly oriented.
    assert "NOTE" in cells[0][0] or "ETON" in cells[0][0]


def test_table_entry_shape(table_pdf):
    result = extract_tables(table_pdf)
    assert 1 in result
    entry = result[1][0]
    assert entry["cells"][0] == ["Rol", "Actiune"]
    assert len(entry["bbox"]) == 4


def test_clean_row_replaces_none():
    assert _clean_row(["Rol", None, "Aprobare"]) == ["Rol", "", "Aprobare"]


def test_is_real_table_rejects_single_column():
    assert _is_real_table([["prose line one"], ["prose line two"]]) is False


def test_is_real_table_rejects_single_row():
    assert _is_real_table([["Rol", "Actiune"]]) is False


def test_is_real_table_accepts_grid():
    assert _is_real_table([["Rol", "Actiune"], ["Manager", "Aproba"]]) is True


def test_is_real_table_rejects_prose_box():
    """A grey title bar over a slide's content box detects as a 2x2 grid, but its
    cells hold paragraphs, not values — see _MAX_MEDIAN_CELL_CHARS."""
    prose = (
        "Mâlurile - pământuri cu un conţinut de materii organice sub 5%. Sunt "
        "depozite aluvionare conţinând în general mai mult de 90% elemente "
        "inferioare dimensiunii de 0,20 mm, alcătuite din particule argiloase."
    )
    assert _is_real_table([[prose, ""], [prose, ""]]) is False


def test_is_real_table_accepts_grid_with_one_long_cell():
    """Length is judged on the median, not the maximum: a real table keeps its
    footnote row."""
    footnote = "[*] Lehr, H. - Fundatii, vol. II, Editura Tehnica, Bucuresti, 1957" * 3
    cells = [
        ["Caracterizare", "Necoeziv", "Coeziv"],
        ["Uscat", "0 - 0,40", "0 - 0,50"],
        ["Umed", "0,40 - 0,80", "0,50 - 0,80"],
        [footnote, "", ""],
    ]
    assert _is_real_table(cells) is True


def test_is_real_table_rejects_all_blank_cells():
    assert _is_real_table([["", "  "], ["", ""]]) is False


def test_is_real_table_rejects_repeated_chart_grid():
    header = ["ERA", "PERIODO", "EPOCH", "SCARA STRATIGRAFICĂ"]
    row = ["CULTARNA Ţ", "CULTARNA Ţ", "CULTARNA Ţ", "CULTARNA Ţ"]
    cells = [header] + [row] * 10
    assert _is_real_table(cells) is False


def test_is_real_table_keeps_small_legitimate_repeat():
    header = ["Item", "Status"]
    cells = [header, ["A", "Yes"], ["B", "Yes"]]
    assert _is_real_table(cells) is True


def _prose_box_pdf(path):
    """A slide's layout: a ruled title bar above a ruled content box holding a
    paragraph. pdfplumber's line detection reads this as a 2x2 grid."""
    doc = pymupdf.open()
    page = doc.new_page()
    x0, x1, top, mid, bottom = 60, 520, 90, 130, 320
    for y in (top, mid, bottom):
        page.draw_line((x0, y), (x1, y))
    for x in (x0, x1):
        page.draw_line((x, top), (x, bottom))
    page.insert_text((x0 + 6, top + 20), "Pamanturi cu continut de materii organice")
    page.insert_textbox(
        pymupdf.Rect(x0 + 6, mid + 6, x1 - 6, bottom - 6),
        "Malurile sunt pamanturi cu un continut de materii organice sub 5%. "
        "Sunt depozite aluvionare continand in general mai mult de 90% elemente "
        "inferioare dimensiunii de 0,20 mm, alcatuite din particule argiloase "
        "foarte fine, afanate si putin consolidate, prezentand in general limite "
        "de curgere ridicate si un indice de plasticitate mare.",
        fontsize=9,
    )
    doc.save(path)
    doc.close()
    return path


def test_prose_box_is_not_extracted_as_a_table(tmp_path):
    assert extract_tables(_prose_box_pdf(tmp_path / "slide.pdf")) == {}


def test_rejected_prose_box_comes_back_as_page_text(tmp_path):
    """The point of rejecting it: pdf_reader excludes accepted tables' bboxes
    from the PyMuPDF text pass, so a false positive doesn't just mangle the
    prose — it deletes it from the text block."""
    from extractor.pdf_reader import extract_pdf

    doc = extract_pdf(_prose_box_pdf(tmp_path / "slide.pdf"))
    blocks = doc["pages"][0]["content"]

    assert not [b for b in blocks if b["type"] == "table"]
    text = "\n".join(b["content"] for b in blocks if b["type"] == "text")
    assert "Malurile sunt pamanturi" in text
    # the tail of the paragraph, past where the box would have been clipped
    assert "un indice de plasticitate" in text
