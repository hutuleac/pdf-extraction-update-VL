"""The two rendering failures found on the 388-page reference course.

Both are language-independent: an alignment body and a stray brace look the
same in any document, which is why neither check knows anything about the
document's language.
"""
from extractor.page_signals import (
    PageSignals,
    _unexpected_scripts,
    warnings_for_page,
)
from extractor.vlm.doctag import as_display_math, formula_is_balanced


def test_alignment_body_gets_its_environment():
    # Real granite output from page 115: a bare align body, which KaTeX
    # rejects with "Expected 'EOF', got '&'".
    latex = r"V _ { 1 } & = \left ( v _ { x } \right ) d y + \\ & + \left ( v _ { y } \right ) d x"
    out = as_display_math(latex)
    assert r"\begin{aligned}" in out and r"\end{aligned}" in out


def test_plain_formula_is_left_alone():
    assert as_display_math("x = 1") == "$$\nx = 1\n$$"


def test_existing_environment_is_not_nested():
    latex = r"\begin{cases} a \\ b \end{cases}"
    assert as_display_math(latex).count(r"\begin{") == 1


def test_trailing_row_separator_is_dropped():
    # An empty final row otherwise renders as a blank line inside the block.
    assert as_display_math(r"a &= 1 \\ b &= 2 \\").rstrip().endswith(r"\end{aligned}" + "\n$$")


def test_stray_closing_brace_is_rejected():
    # Real granite output from page 34 — balanced \left/\right, broken braces.
    assert not formula_is_balanced(r"{ \bullet } & S _ { K r u m b e i n } = } & \sqrt { A }")


def test_escaped_braces_are_literal():
    assert formula_is_balanced(r"\{ a \} + { b }")


def test_mismapped_symbol_font_is_reported_without_reclassifying():
    # Page 41's real glyphs: one symbol font decoded across four scripts.
    scripts = _unexpected_scripts("௙ൌ ߪ െ ݑ · ݐ݃ ∅ᇱ, a pământului")
    assert len(scripts) >= 2
    warnings = warnings_for_page(
        PageSignals(text_chars=1374, mismapped_scripts=scripts),
        "native-text", 41,
    )
    codes = [w["code"] for w in warnings]
    assert "MISMAPPED_GLYPHS" in codes
    # The page keeps its class: the prose is sound, only the symbols are not.
    assert "GARBLED_TEXT" not in codes


def test_a_single_quoted_script_is_not_mismapped():
    # An English document quoting one foreign phrase must stay silent.
    assert len(_unexpected_scripts("The kanji 日本語 means Japanese.")) < 2
