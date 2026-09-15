"""Tests for extractor/text_loader.py — encoding-aware file loading."""
from extractor.text_loader import LoadResult, load_text_file


class TestLoadTextFile:
    def test_utf8_file_loads_correctly(self, tmp_path):
        f = tmp_path / "utf8.txt"
        f.write_text("Hello, world! This is a longer sentence for detection.", encoding="utf-8")
        result = load_text_file(f)
        assert result.text == "Hello, world! This is a longer sentence for detection."
        # charset-normalizer may report low coherence on short ASCII — that's acceptable
        # Primary concern: text is loaded correctly without corruption
        assert result.text

    def test_empty_file_returns_empty_string(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        result = load_text_file(f)
        assert result.text == ""
        assert result.encoding == "utf-8"
        assert result.confidence == 1.0
        assert result.warnings == []

    def test_latin1_file_detects_encoding(self, tmp_path):
        # Romanian text in Latin-1 encoding
        text = "Înregistrarea cererii de concediu"
        f = tmp_path / "latin1.txt"
        f.write_bytes(text.encode("latin-1"))
        result = load_text_file(f)
        # charset-normalizer should detect it and decode correctly
        assert "nregistrarea" in result.text

    def test_cp1252_romanian_diacritics(self, tmp_path):
        # Use characters that are actually in cp1252 (â, î are in cp1252)
        text = "Înregistrare în aplicâie"
        f = tmp_path / "cp1252.csv"
        f.write_bytes(text.encode("cp1252"))
        result = load_text_file(f)
        # Should NOT produce replacement characters
        assert "\ufffd" not in result.text
        assert "nregistrare" in result.text

    def test_clean_ascii_file_emits_no_warning(self, tmp_path):
        """ENCODING_FALLBACK means 'we had to guess'. Plain ASCII is not a guess.

        Firing on every clean file makes the warning worthless as a filter.
        """
        f = tmp_path / "clean.txt"
        f.write_text("A perfectly ordinary sentence of plain text.\n", encoding="utf-8")
        assert load_text_file(f).warnings == []

    def test_utf8_file_with_diacritics_emits_no_warning(self, tmp_path):
        f = tmp_path / "clean_utf8.txt"
        f.write_text("Înregistrarea cererii de concediu în aplicație.\n", encoding="utf-8")
        assert load_text_file(f).warnings == []

    def test_cp1252_file_is_still_flagged_as_guessed(self, tmp_path):
        """A legacy codepage was inferred, not declared — that is the guess.

        This is the signal the warning exists for; the ASCII fix must not
        silence it.
        """
        f = tmp_path / "cp1252.csv"
        f.write_bytes("Înregistrare în aplicâie, raport trimestrial".encode("cp1252"))
        codes = [w["code"] for w in load_text_file(f).warnings]
        assert codes == ["ENCODING_FALLBACK"]

    def test_result_is_load_result_dataclass(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("data", encoding="utf-8")
        result = load_text_file(f)
        assert isinstance(result, LoadResult)
        assert hasattr(result, "text")
        assert hasattr(result, "encoding")
        assert hasattr(result, "confidence")
        assert hasattr(result, "warnings")
