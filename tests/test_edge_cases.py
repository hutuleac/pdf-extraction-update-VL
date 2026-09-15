"""Edge-case probes — written against what the output *should* contain.

Tests marked ``xfail(strict=True)`` document a confirmed defect from
``docs/code-audit-2026-08-31.md``. They are executable evidence: the suite stays
green today, and each one turns into a failure the moment the defect is fixed,
which is the signal to delete the marker.

Everything unmarked is a passing regression guard.
"""
from __future__ import annotations

import pytest

from extractor.csv_reader import extract_csv
from extractor.dispatcher import extract_document
from extractor.headers_footers import remove_lines_from_text
from extractor.json_reader import extract_json
from extractor.markdown_writer import _render_table, write_markdown
from extractor.normalizer import normalize
from extractor.reading_order import reorder_words
from extractor.warning_text import describe

# Written as escapes on purpose: a literal non-breaking or zero-width space in
# source is invisible to a reviewer and ambiguous to the linter.
NBSP = "\N{NO-BREAK SPACE}"
ZERO_WIDTH_SPACE = "\N{ZERO WIDTH SPACE}"


def _text_of(model: dict) -> str:
    """All text-block content in a model, newline-joined."""
    return "\n".join(
        block["content"]
        for unit in model["pages"]
        for block in unit["content"]
        if block["type"] == "text"
    )


def _tables_of(model: dict) -> list[list[list[str]]]:
    """Every table block in a model."""
    return [
        block["content"]
        for unit in model["pages"]
        for block in unit["content"]
        if block["type"] == "table"
    ]


def _word(x0, y0, x1, y1, text, block_no, line_no):
    """One tuple in the shape page.get_text('words') returns."""
    return (x0, y0, x1, y1, text, block_no, line_no, 0)


# ---------------------------------------------------------------------------
# CSV: quoted fields spanning lines
# ---------------------------------------------------------------------------

class TestCsvMultilineFields:
    def test_quoted_newline_field_stays_one_cell(self, tmp_path):
        """RFC-4180 allows a newline inside a quoted field; it is one cell."""
        f = tmp_path / "multiline.csv"
        f.write_text('name,note\n"Alice","line one\nline two"\n', encoding="utf-8")

        rows = _tables_of(extract_csv(f))[0]

        assert rows[0] == ["name", "note"]
        assert rows[1] == ["Alice", "line one\nline two"]

    def test_quoted_delimiter_stays_one_cell(self, tmp_path):
        """A comma inside quotes must not split the cell."""
        f = tmp_path / "quoted.csv"
        f.write_text('a,b\n"x,y",z\n', encoding="utf-8")

        assert _tables_of(extract_csv(f))[0][1] == ["x,y", "z"]


# ---------------------------------------------------------------------------
# Reading order: full-width title above two columns
# ---------------------------------------------------------------------------

class TestReadingOrderWithTitle:
    """A banner title spanning both columns is the most common report layout."""

    @staticmethod
    def _title_over_two_columns() -> list[tuple]:
        # The right column starts slightly higher than the left, so a purely
        # top-to-bottom sort interleaves them instead of reading each in turn.
        return [
            _word(50, 50, 550, 60, "TITLE", 0, 0),
            _word(50, 100, 250, 110, "LEFTA", 1, 0),
            _word(50, 120, 250, 130, "LEFTB", 1, 1),
            _word(300, 90, 550, 100, "RIGHTA", 2, 0),
            _word(300, 110, 550, 120, "RIGHTB", 2, 1),
        ]

    def test_detects_two_columns(self):
        _ordered, num_columns, _concurrent = reorder_words(self._title_over_two_columns())
        assert num_columns == 2

    def test_left_column_read_before_right(self):
        ordered, _num_columns, _concurrent = reorder_words(self._title_over_two_columns())
        words = [w for w, _b, _l in ordered]
        assert words.index("LEFTB") < words.index("RIGHTA")

    def test_plain_two_columns_still_detected(self):
        """Regression guard: with no title the clustering works correctly."""
        words = [
            _word(50, 100, 250, 110, "LEFTA", 0, 0),
            _word(50, 120, 250, 130, "LEFTB", 0, 1),
            _word(300, 100, 550, 110, "RIGHTA", 1, 0),
        ]
        _ordered, num_columns, concurrent = reorder_words(words)
        assert num_columns == 2
        assert concurrent  # left/right blocks overlap vertically (y 100-130 vs 100-110)

    def test_single_column_unchanged(self):
        """Regression guard named in reading_order's own docstring."""
        words = [
            _word(50, 100, 250, 110, "ONE", 0, 0),
            _word(50, 120, 250, 130, "TWO", 0, 1),
        ]
        ordered, num_columns, concurrent = reorder_words(words)
        assert num_columns == 1
        assert concurrent is False
        assert [w for w, _b, _l in ordered] == ["ONE", "TWO"]

    def test_empty_word_list_is_safe(self):
        assert reorder_words([]) == ([], 1, False)

    def test_stacked_columns_are_not_concurrent(self):
        """Two 'columns' that occupy different vertical bands aren't a real
        side-by-side split — one runs entirely above the other, so there is
        no interleaving risk and no warning is warranted."""
        words = [
            _word(50, 100, 250, 110, "TOP", 0, 0),
            _word(300, 200, 550, 210, "BOTTOM", 1, 0),
        ]
        _ordered, num_columns, concurrent = reorder_words(words)
        assert num_columns == 2
        assert concurrent is False


# ---------------------------------------------------------------------------
# Header/footer removal
# ---------------------------------------------------------------------------

class TestHeaderFooterRemoval:
    def test_body_line_matching_header_is_kept(self):
        """Only the banner occurrence should go, not a matching body heading."""
        text = "SPECIFICATION\nIntro paragraph.\nSPECIFICATION\nDetail paragraph."
        cleaned = remove_lines_from_text(text, ["SPECIFICATION"])

        assert "Intro paragraph." in cleaned
        assert cleaned.count("SPECIFICATION") == 1

    def test_no_lines_to_remove_returns_text_unchanged(self):
        assert remove_lines_from_text("a\nb", []) == "a\nb"


# ---------------------------------------------------------------------------
# Markdown table rendering
# ---------------------------------------------------------------------------

class TestMarkdownTableRendering:
    def test_row_wider_than_header_keeps_all_cells(self):
        assert "3" in _render_table([["A", "B"], ["1", "2", "3"]])

    def test_row_narrower_than_header_is_padded(self):
        md = _render_table([["A", "B", "C"], ["1"]])
        assert md.splitlines()[2] == "| 1 |  |  |"

    def test_empty_rows_render_without_crashing(self):
        assert _render_table([]) == ""
        assert _render_table([[]]) is not None

    def test_unit_with_no_blocks_still_gets_a_heading(self, tmp_path):
        model = {
            "document": {"filename": "e.pdf", "source_type": "pdf", "pages": 1,
                         "author": "", "title": "", "has_images": False,
                         "image_count": 0},
            "pages": [{"unit": 1, "unit_type": "page", "has_images": False,
                       "image_count": 0, "content": []}],
        }
        assert "## Page 1" in write_markdown(model, tmp_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Warning rendering robustness
# ---------------------------------------------------------------------------

class TestWarningDescribe:
    """describe() runs after extraction succeeded.

    It is called by the Markdown writer and the CLI summary, so an exception
    here loses a document whose content was already extracted correctly.
    """

    @pytest.mark.parametrize("warning", [
        pytest.param({"code": "OCR_MIXED_CONFIDENCE"}, id="mixed-no-counts"),
        pytest.param({"code": "OCR_NOISE_FILTERED"}, id="noise-no-sample"),
        pytest.param({"code": "SOMETHING_NEW", "page": 2}, id="unknown-code"),
        pytest.param({"code": "OCR_APPLIED", "confidence": 0.9}, id="applied"),
        pytest.param({"code": "GARBLED_TEXT", "page": 1}, id="page-scoped"),
    ])
    def test_never_raises_on_incomplete_warning(self, warning):
        assert isinstance(describe(warning), str)

    def test_unknown_code_falls_back_to_the_code_itself(self):
        assert "SOMETHING_NEW" in describe({"code": "SOMETHING_NEW"})


# ---------------------------------------------------------------------------
# Normalizer edge cases
# ---------------------------------------------------------------------------

class TestNormalizerEdgeCases:
    @pytest.mark.parametrize("raw,expected", [
        pytest.param(f"a{NBSP}{NBSP}b", "a b", id="double-nbsp"),
        pytest.param(f"a{ZERO_WIDTH_SPACE}b", "ab", id="zero-width-space"),
        pytest.param("tab\t\tsep", "tab sep", id="double-tab"),
        pytest.param("a  b", "a b", id="double-space"),
    ])
    def test_invisible_whitespace_is_normalized(self, raw, expected):
        assert normalize(raw) == expected

    def test_whitespace_only_input_is_empty(self):
        assert normalize("   \n\t\n  ") == ""

    def test_control_characters_removed(self):
        assert "\x00" not in normalize("before\x00after")

    def test_blank_lines_collapse(self):
        assert normalize("a\n\n\n\nb") == "a\nb"


# ---------------------------------------------------------------------------
# JSON reader
# ---------------------------------------------------------------------------

class TestJsonReader:
    def test_records_with_nested_values_are_not_python_repr(self, tmp_path):
        f = tmp_path / "nested.json"
        f.write_text('[{"a": 1, "b": {"x": 2}}, {"a": 3, "b": {"x": 4}}]', encoding="utf-8")

        model = extract_json(f)
        rendered = str(_tables_of(model)) + _text_of(model)
        assert "'x'" not in rendered

    def test_empty_list_produces_valid_document(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("[]", encoding="utf-8")
        assert extract_json(f)["document"]["source_type"] == "json"

    def test_top_level_scalar_produces_valid_document(self, tmp_path):
        f = tmp_path / "scalar.json"
        f.write_text('"just a string"', encoding="utf-8")
        assert "just a string" in _text_of(extract_json(f))

    def test_malformed_json_raises_value_error(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            extract_json(f)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class TestDispatcherRouting:
    def test_uppercase_extension_routes(self, tmp_path):
        f = tmp_path / "SHOUTY.TXT"
        f.write_text("content here", encoding="utf-8")
        assert extract_document(f)["document"]["source_type"] == "txt"

    def test_unsupported_extension_raises_value_error(self, tmp_path):
        f = tmp_path / "thing.exe"
        f.write_bytes(b"MZ")
        with pytest.raises(ValueError, match="Unsupported file type"):
            extract_document(f)

    def test_no_extension_raises_value_error(self, tmp_path):
        f = tmp_path / "README"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError):
            extract_document(f)


# ---------------------------------------------------------------------------
# Model invariants — every reader must satisfy these
# ---------------------------------------------------------------------------

class TestModelInvariants:
    """Contract checks that apply to every format, asserted in one place."""

    @pytest.fixture
    def one_of_each(self, tmp_path) -> dict[str, object]:
        """One small file per text-based format, keyed by extension."""
        (tmp_path / "a.txt").write_text("Plain text body.", encoding="utf-8")
        (tmp_path / "a.md").write_text("# Heading\n\nBody.", encoding="utf-8")
        (tmp_path / "a.csv").write_text("h1,h2\nv1,v2\n", encoding="utf-8")
        (tmp_path / "a.json").write_text('{"k": "v"}', encoding="utf-8")
        (tmp_path / "a.xml").write_text("<root><child>v</child></root>", encoding="utf-8")
        (tmp_path / "a.html").write_text(
            "<html><body><p>Body.</p></body></html>", encoding="utf-8",
        )
        return {p.suffix: p for p in tmp_path.iterdir()}

    def test_unit_numbers_are_sequential_from_one(self, one_of_each):
        for suffix, path in one_of_each.items():
            units = extract_document(path)["pages"]
            assert [u["unit"] for u in units] == list(range(1, len(units) + 1)), suffix

    def test_document_pages_matches_unit_count(self, one_of_each):
        for suffix, path in one_of_each.items():
            model = extract_document(path)
            assert model["document"]["pages"] == len(model["pages"]), suffix

    def test_every_block_has_a_known_type(self, one_of_each):
        known = {"text", "table", "header", "footer"}
        for suffix, path in one_of_each.items():
            for unit in extract_document(path)["pages"]:
                for block in unit["content"]:
                    assert block["type"] in known, f"{suffix}: {block['type']}"

    def test_no_empty_text_block_is_emitted(self, one_of_each):
        """make_text_block returns None for empty content, so none should ship."""
        for suffix, path in one_of_each.items():
            for unit in extract_document(path)["pages"]:
                for block in unit["content"]:
                    if block["type"] == "text":
                        assert block["content"].strip(), suffix

    def test_every_warning_has_a_code(self, one_of_each):
        for suffix, path in one_of_each.items():
            for warning in extract_document(path)["document"].get("warnings", []):
                assert warning.get("code"), suffix

    def test_every_emitted_warning_code_has_wording(self, one_of_each):
        """A code with no template renders as the bare code — unreadable."""
        from extractor.warning_text import _TEMPLATES

        for suffix, path in one_of_each.items():
            for warning in extract_document(path)["document"].get("warnings", []):
                assert warning["code"] in _TEMPLATES, f"{suffix}: {warning['code']}"


# ---------------------------------------------------------------------------
# Clean inputs must be quiet — the assertion whose absence hid the ASCII bug
# ---------------------------------------------------------------------------

class TestCleanInputsProduceNoWarnings:
    """A warning that fires on every clean file filters nothing."""

    @pytest.mark.parametrize("name,content", [
        ("clean.txt", "A perfectly ordinary sentence of plain text.\n"),
        ("clean.csv", "id,name,qty\n1,widget,5\n2,gadget,9\n"),
        ("clean.md", "# Title\n\nA paragraph of prose.\n"),
        ("clean.json", '{"key": "value", "count": 3}'),
    ], ids=["txt", "csv", "md", "json"])
    def test_clean_utf8_file_emits_no_warnings(self, tmp_path, name, content):
        f = tmp_path / name
        f.write_text(content, encoding="utf-8")

        warnings = extract_document(f)["document"].get("warnings", [])
        assert warnings == []
