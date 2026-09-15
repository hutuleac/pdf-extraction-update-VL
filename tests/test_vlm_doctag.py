# tests/test_vlm_doctag.py
"""Parsing and validating granite-docling's doctag output.

The samples below are real output captured in the 2026-09-05 spike — see
docs/spike-2026-09-05-granite-docling.md.
"""
from extractor.vlm.doctag import (
    formula_is_balanced,
    is_truncated,
    parse,
    strip_locations,
)

PAGE_046 = (
    "<doctag><page_header><loc_49><loc_21><loc_255><loc_28>Stanciu A. • Lungu I."
    "</page_header>\n"
    "<section_header_level_1><loc_67><loc_36><loc_253><loc_44>"
    "Pamanturi cu continut de materii organice</section_header_level_1>\n"
    "<unordered_list><list_item><loc_66><loc_47><loc_431><loc_77>"
    "• Malurile - pamanturi cu un continut de materii organice sub 5%."
    "</list_item>\n"
    "<list_item><loc_66><loc_79><loc_431><loc_93>"
    "• Namolurile - pamanturi asemanatoare malurilor.</list_item>\n"
    "</unordered_list>\n"
    "<picture><loc_222><loc_50><loc_426><loc_245></picture>\n"
    "<page_footer><loc_449><loc_470><loc_456><loc_477>5</page_footer>\n"
    "</doctag>"
)

PAGE_073_FORMULA = (
    "<doctag><text><loc_10><loc_10><loc_20><loc_20>unde</text>\n"
    "<formula>w = \\frac { M _ { w } } { M _ { s } } 1 0 0 = 8 , 8 2 \\%</formula>\n"
    "</doctag>"
)

PAGE_058_TABLE = (
    "<doctag><otsl><ched>Caracterizare<ched>Necoeziv<ched>Coeziv<nl>"
    "<fcel>Uscat<fcel>0 - 0,40<fcel>0 - 0,50<nl>"
    "<fcel>Umed<fcel>0,40 - 0,80<fcel>0,50 - 0,80<nl></otsl></doctag>"
)

# Page 176: one Egorov formula degenerated into a repeating fragment that ate
# the whole token budget. The tag is never closed and neither is </doctag>.
PAGE_176_TRUNCATED = (
    "<doctag><section_header_level_1><loc_1><loc_2><loc_3><loc_4>Metoda Egorov"
    "</section_header_level_1>\n"
    "<formula>\\left [ \\frac { z } { b } \\right ] \\left [ \\frac { z } { b }"
)


def test_strip_locations_removes_coordinate_tags():
    assert "<loc_" not in strip_locations(PAGE_046)
    assert "Stanciu A." in strip_locations(PAGE_046)


def test_is_truncated_false_for_complete_output():
    assert is_truncated(PAGE_046) is False


def test_is_truncated_true_when_doctag_never_closes():
    assert is_truncated(PAGE_176_TRUNCATED) is True


def test_is_truncated_true_when_a_tag_is_left_open():
    """Closing </doctag> is not enough if a formula inside it never closed."""
    assert is_truncated("<doctag><formula>x = 1</doctag>") is True


def test_formula_balance_accepts_matched_delimiters():
    assert formula_is_balanced(r"\left( \frac{a}{b} \right)") is True


def test_formula_balance_rejects_unmatched_delimiters():
    """6 of 127 formulas in the spike had \\left without \\right."""
    assert formula_is_balanced(r"\left[ \frac{z}{b}") is False


def test_formula_balance_ignores_formulas_without_delimiters():
    assert formula_is_balanced("a = b + c") is True


def test_parse_joins_prose_in_reading_order():
    result = parse(PAGE_046)

    assert "Pamanturi cu continut de materii organice" in result.text
    assert "• Malurile" in result.text
    assert "• Namolurile" in result.text
    # Order is the order the model emitted, which is reading order.
    assert result.text.index("Malurile") < result.text.index("Namolurile")


def test_parse_drops_page_headers_and_footers():
    """The native pipeline already detects and separates these; taking the
    model's copy too would duplicate them into the prose."""
    result = parse(PAGE_046)

    assert "Stanciu A." not in result.text
    assert result.text.strip() != ""


def test_parse_records_a_picture_without_inventing_a_caption():
    result = parse(PAGE_046)

    assert result.has_picture is True
    assert "picture" not in result.text.lower()


def test_parse_wraps_formulas_as_display_math():
    result = parse(PAGE_073_FORMULA)

    assert result.formula_count == 1
    assert result.rejected_formulas == 0
    assert "$$" in result.text
    assert r"\frac { M _ { w } } { M _ { s } }" in result.text
    assert "unde" in result.text


def test_parse_drops_an_unbalanced_formula_and_counts_it():
    """An unbalanced formula is never emitted as math — a wrong equation that
    renders is worse than a missing one, because nothing flags it."""
    raw = "<doctag><text><loc_1><loc_1><loc_1><loc_1>x</text>" \
          "<formula>\\left[ \\frac{z}{b}</formula></doctag>"

    result = parse(raw)

    assert result.formula_count == 0
    assert result.rejected_formulas == 1
    assert "$$" not in result.text


def test_parse_extracts_an_otsl_table_as_rows():
    result = parse(PAGE_058_TABLE)

    assert result.tables == [[
        ["Caracterizare", "Necoeziv", "Coeziv"],
        ["Uscat", "0 - 0,40", "0 - 0,50"],
        ["Umed", "0,40 - 0,80", "0,50 - 0,80"],
    ]]
    # Table content must not also appear in the prose, or it lands twice.
    assert "Uscat" not in result.text


def test_parse_handles_span_and_empty_cells():
    raw = ("<doctag><otsl><ched>A<ched>B<lcel><nl>"
           "<ecel><fcel>2<fcel>3<nl></otsl></doctag>")

    result = parse(raw)

    assert result.tables == [[["A", "B", ""], ["", "2", "3"]]]


def test_parse_of_empty_output_is_empty_not_an_error():
    result = parse("<doctag></doctag>")

    assert result.text == ""
    assert result.tables == []
    assert result.formula_count == 0
