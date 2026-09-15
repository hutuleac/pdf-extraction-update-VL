"""Extended tests for normalizer — covers normalize_with_report and ftfy integration."""
import pytest

from extractor.normalizer import NormalizeResult, normalize, normalize_with_report


class TestNormalizeWithReport:
    def test_clean_text_no_repair(self):
        result = normalize_with_report("Hello world")
        assert result.text == "Hello world"
        assert result.unicode_repaired is False
        assert result.warnings == []

    def test_empty_string(self):
        result = normalize_with_report("")
        assert result.text == ""
        assert result.unicode_repaired is False

    def test_mojibake_repaired(self):
        # "naïve" mojibaked as latin-1 interpreted as utf-8
        mojibake = "naÃ¯ve"
        result = normalize_with_report(mojibake)
        assert result.unicode_repaired is True
        assert {"code": "UNICODE_REPAIRED"} in result.warnings
        assert "naïve" in result.text

    def test_returns_normalize_result_dataclass(self):
        result = normalize_with_report("test")
        assert isinstance(result, NormalizeResult)

    def test_normalize_wrapper_matches_report_text(self):
        text = "some-\ntext  with   spaces"
        assert normalize(text) == normalize_with_report(text).text


@pytest.mark.parametrize("input_text,expected", [
    ("procedu-\nra", "procedura"),
    # A hyphen followed by a space is punctuation between two words, not a
    # word wrapped across a line — rejoining it would invent "procedura".
    ("procedu- ra", "procedu- ra"),
    ("Manager     aproba", "Manager aproba"),
    ("Paragraf\n\n\nParagraf", "Paragraf\nParagraf"),
    ("   text   ", "text"),
], ids=[
    "hyphenation-newline",
    "spaced-hyphen-kept",
    "collapse-spaces",
    "remove-empty-lines",
    "trim-whitespace",
])
def test_normalize_parametrized(input_text, expected):
    assert normalize(input_text) == expected
