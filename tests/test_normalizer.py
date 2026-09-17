from extractor.normalizer import normalize


def test_fix_hyphenation_across_newline():
    assert normalize("procedu-\nra") == "procedura"


def test_cedilla_diacritics_are_canonicalized_to_comma_below():
    assert normalize("Ţinţa e Ştiinţa") == "Țința e Știința"


def test_private_use_area_glyphs_are_dropped():
    # A Symbol-font bullet/operator glyph mapped into the Private Use
    # Area (here U+F0B7) carries no meaning outside that font.
    assert normalize("Bullet\uf0b7point") == "Bulletpoint"
    assert normalize("\U000f0001") == ""


def test_fix_hyphenation_across_crlf():
    assert normalize("procedu-\r\nra") == "procedura"


def test_fix_hyphenation_across_newline_with_indentation():
    assert normalize("procedu-  \n   ra") == "procedura"


def test_spaced_hyphen_inside_a_line_is_not_a_word_break():
    """'well- known' is two words loosely typeset, not one word wrapped.

    Rejoining them invents a word that is nowhere in the source.
    """
    assert normalize("well- known") == "well- known"
    assert normalize("co- operate") == "co- operate"
    assert normalize("state- of-the-art") == "state- of-the-art"


def test_line_break_after_a_non_letter_is_not_a_hyphenated_word():
    """Only letters wrap mid-word — an OCR misread like '0-' must not glue on."""
    assert normalize("0-\n[LOG] entry") == "0-\n[LOG] entry"


def test_dash_between_spaced_words_is_kept():
    """'well - known' is punctuation, not a word broken across a line."""
    assert normalize("well - known") == "well - known"


def test_hyphenated_compound_on_one_line_is_left_alone():
    assert normalize("state-of-the-art") == "state-of-the-art"


def test_collapse_multiple_spaces():
    assert normalize("Manager     aproba") == "Manager aproba"


def test_blank_line_runs_collapse_to_one_paragraph_break():
    assert normalize("Paragraf\n\n\nParagraf") == "Paragraf\n\nParagraf"


def test_single_newlines_are_kept_as_line_breaks():
    assert normalize("linia 1\nlinia 2") == "linia 1\nlinia 2"


def test_whitespace_only_lines_count_as_blank():
    assert normalize("a\n   \n\t\nb") == "a\n\nb"


def test_preserve_layout_keeps_indentation_and_spacing():
    source = "# T\n\n- a\n    - b\n\n```\nx    =  1\n```\n\n\n\nend  "
    assert normalize(source, preserve_layout=True) == (
        "# T\n\n- a\n    - b\n\n```\nx    =  1\n```\n\nend"
    )


def test_trim_whitespace():
    assert normalize("   text   ") == "text"


def test_combined():
    raw = "  Managerul   aproba\n\n\ncere-\nrea de concediu.  "
    assert normalize(raw) == "Managerul aproba\n\ncererea de concediu."
