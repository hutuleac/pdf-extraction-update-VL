"""Reading PaddleOCR-VL's Markdown back into the shape doctag produces."""
import pytest

from extractor.vlm import markdown_doc, models
from extractor.vlm.base import UnavailableReason, VlmUnavailable

# --- tables ----------------------------------------------------------------

def test_pipe_table_is_read_without_its_separator_row():
    result = markdown_doc.parse(
        "| Material | Range |\n|---|---|\n| Uscat | 0 - 0,40 |\n"
    )
    assert result.tables == [[["Material", "Range"], ["Uscat", "0 - 0,40"]]]


def test_table_cells_are_not_repeated_in_the_prose():
    result = markdown_doc.parse(
        "Intro line.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nClosing line."
    )
    assert result.text == "Intro line.\nClosing line."


def test_a_sentence_containing_a_pipe_is_not_a_table():
    """Without a separator row there is no evidence of a table, only prose."""
    result = markdown_doc.parse("Set the flag | value pair before starting.")
    assert result.tables == []
    assert "value pair" in result.text


def test_html_table_is_read_and_stripped_of_markup():
    result = markdown_doc.parse(
        "<table><tr><th>N</th><th>V</th></tr>"
        "<tr><td><b>1</b></td><td>2</td></tr></table>"
    )
    assert result.tables == [[["N", "V"], ["1", "2"]]]
    assert result.text == ""


# --- formulas --------------------------------------------------------------

def test_formulas_are_stored_bare_so_apply_can_wrap_them():
    """apply.py adds the $$ itself when rebuilding a redundant page."""
    result = markdown_doc.parse("$$a = \\frac { b } { c }$$")
    assert result.formulas == ["a = \\frac { b } { c }"]
    assert result.formula_count == 1


def test_formula_is_wrapped_once_in_the_prose():
    result = markdown_doc.parse("Before.\n\n$$x = 1$$\n\nAfter.")
    assert result.text == "Before.\n$$\nx = 1\n$$\nAfter."


def test_bracket_delimited_formula_is_read_too():
    result = markdown_doc.parse("\\[ y = 2 \\]")
    assert result.formulas == ["y = 2"]


def test_unbalanced_formula_is_dropped_not_emitted():
    result = markdown_doc.parse("$$\\left[ \\frac{z}{b}$$")
    assert result.formulas == []
    assert result.rejected_formulas == 1
    assert "frac" not in result.text


def test_inline_math_stays_in_its_sentence():
    result = markdown_doc.parse("The value $x$ is fixed.")
    assert result.formulas == []
    assert result.text == "The value $x$ is fixed."


# --- pictures and prose ----------------------------------------------------

def test_image_marks_the_page_and_leaves_no_markup_behind():
    result = markdown_doc.parse("![diagram](fig1.png)\n\nCaption text.")
    assert result.has_picture is True
    assert result.text == "Caption text."


def test_empty_output_parses_to_nothing():
    result = markdown_doc.parse("")
    assert result.text == ""
    assert result.tables == []


# --- layout tokens ---------------------------------------------------------

def test_layout_tokens_are_stripped_from_the_prose():
    """Measured on a real page: 120 of these reached the text before the strip."""
    result = markdown_doc.parse(
        "GEOTEHNICA - note de curs<|LOC_664|><|LOC_40|>\nBody line."
    )
    assert result.text == "GEOTEHNICA - note de curs\nBody line."


def test_a_layout_token_cannot_corrupt_a_formula():
    result = markdown_doc.parse("$$a<|LOC_12|> = b$$")
    assert result.formulas == ["a = b"]


# --- truncation ------------------------------------------------------------

def test_markdown_never_claims_truncation_on_its_own():
    """The engine's token-cap signal is the guard for this format."""
    assert markdown_doc.is_truncated("a sentence cut off mid-") is False


# --- model routing ---------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "parser"),
    [
        ("mlx-community/PaddleOCR-VL-1.6-4bit", "extractor.vlm.markdown_doc"),
        ("ibm-granite/granite-docling-258M-mlx", "extractor.vlm.doctag"),
    ],
)
def test_model_name_selects_its_parser(name, parser):
    assert models.parser_for(name).__name__ == parser


def test_an_unknown_model_is_refused_rather_than_guessed_at():
    """Falling through to the wrong parser would empty every page silently."""
    with pytest.raises(VlmUnavailable) as caught:
        models.for_model("some/unknown-vlm")
    assert caught.value.reason is UnavailableReason.UNKNOWN_MODEL
    assert "paddleocr-vl" in str(caught.value)


def test_inline_formulas_are_counted():
    # This model writes most of its maths inline: 486 against 57 display
    # formulas on the reference course. They were invisible to the count.
    parsed = markdown_doc.parse(r"for z = 0, \( p_a = q \cdot K_a = 10,15 \) kN/m squared.")
    assert parsed.formula_count == 1
    assert parsed.formulas == [r"p_a = q \cdot K_a = 10,15"]


def test_inline_formulas_stay_inside_their_sentence():
    # Lifting one out to a display block would cut the sentence in half: the
    # unit that follows it belongs to the same clause.
    raw = r"for z = 0, \( p_a = 10,15 \) kN/m squared."
    assert markdown_doc.parse(raw).text == raw


def test_unbalanced_inline_formula_is_counted_but_not_cut_out():
    # Unlike a display formula, which stands alone and is deleted, removing
    # this would leave a hole that changes what the sentence says.
    raw = r"Given \( a = { b \) we conclude."
    parsed = markdown_doc.parse(raw)
    assert parsed.rejected_formulas == 1
    assert parsed.formula_count == 0
    assert parsed.text == raw


def test_inline_delimiters_inside_a_display_formula_are_not_double_counted():
    parsed = markdown_doc.parse(r"$$ x = \left( a \right) \(y\) $$")
    assert parsed.formula_count == 1
