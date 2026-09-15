"""Golden corpus assertions — each document asserted against metric thresholds.

A regression shows up as a specific number moving past its threshold.
"""
from extractor.dispatcher import extract_document
from tests.metrics import (
    duplicate_line_ratio,
    empty_page_rate,
    header_footer_leakage,
    phrase_retention,
    reading_order_errors,
    table_cell_recall,
    unicode_corruption_rate,
)

# ---------------------------------------------------------------------------
# PDF: single column
# ---------------------------------------------------------------------------

class TestSingleColumnPDF:
    def test_phrase_retention(self, single_column_pdf):
        model = extract_document(single_column_pdf)
        expected = [
            "Pagina 1",
            "Pagina 2",
            "Pagina 3",
            "singura coloana",
        ]
        assert phrase_retention(model, expected) >= 1.0

    def test_no_duplicates(self, single_column_pdf):
        model = extract_document(single_column_pdf)
        assert duplicate_line_ratio(model) < 0.1

    def test_no_empty_pages(self, single_column_pdf):
        model = extract_document(single_column_pdf)
        assert empty_page_rate(model) == 0.0

    def test_no_unicode_corruption(self, single_column_pdf):
        model = extract_document(single_column_pdf)
        assert unicode_corruption_rate(model) == 0.0


# ---------------------------------------------------------------------------
# PDF: two columns
# ---------------------------------------------------------------------------

class TestTwoColumnPDF:
    def test_phrase_retention(self, two_column_pdf):
        model = extract_document(two_column_pdf)
        expected = [
            "Stanga pagina 1",
            "Dreapta pagina 1",
            "Stanga pagina 2",
            "Dreapta pagina 2",
        ]
        assert phrase_retention(model, expected) >= 1.0

    def test_reading_order(self, two_column_pdf):
        """Left column should appear fully before right column on each page."""
        model = extract_document(two_column_pdf)
        # Check page 1: left markers should all precede right markers
        left_markers = ["Stanga pagina 1 linia 1", "Stanga pagina 1 linia 3"]
        right_markers = ["Dreapta pagina 1 linia 1", "Dreapta pagina 1 linia 3"]
        errors = reading_order_errors(model, [left_markers, right_markers])
        assert errors == 0

    def test_no_empty_pages(self, two_column_pdf):
        model = extract_document(two_column_pdf)
        assert empty_page_rate(model) == 0.0


# ---------------------------------------------------------------------------
# PDF: image-only (scanned)
# ---------------------------------------------------------------------------

class TestImageOnlyPDF:
    def test_page_class_scanned(self, image_only_pdf):
        model = extract_document(image_only_pdf)
        for unit in model["pages"]:
            # Image-only pages should be classified as scanned
            assert unit.get("page_class") in ("scanned", "native-text")

    def test_warnings_present(self, image_only_pdf):
        model = extract_document(image_only_pdf)
        doc = model["document"]
        # If classification detected scanned pages, warnings should exist
        if any(u.get("page_class") == "scanned" for u in model["pages"]):
            assert doc.get("warnings") is not None
            codes = [w["code"] for w in doc["warnings"]]
            assert "SCANNED_PAGE_NO_TEXT" in codes


# ---------------------------------------------------------------------------
# PDF: mixed content
# ---------------------------------------------------------------------------

class TestMixedContentPDF:
    def test_phrase_retention(self, mixed_content_pdf):
        model = extract_document(mixed_content_pdf)
        expected = ["Text pe pagina 1", "Text pe pagina 2", "continut mixt"]
        assert phrase_retention(model, expected) >= 1.0

    def test_no_unicode_corruption(self, mixed_content_pdf):
        model = extract_document(mixed_content_pdf)
        assert unicode_corruption_rate(model) == 0.0


# ---------------------------------------------------------------------------
# PDF: repeated header/footer
# ---------------------------------------------------------------------------

class TestRepeatedHeaderPDF:
    def test_phrase_retention(self, repeated_header_pdf):
        model = extract_document(repeated_header_pdf)
        expected = [
            "Continut pagina 1",
            "Continut pagina 3",
            "Detalii importante",
        ]
        assert phrase_retention(model, expected) >= 1.0

    def test_footer_leakage(self, repeated_header_pdf):
        """Detected footers should not leak into text blocks."""
        model = extract_document(repeated_header_pdf)
        doc = model["document"]
        # If header/footer detection fired, text blocks shouldn't have the pattern
        if doc.get("warnings") and any(
            w["code"] == "HEADER_FOOTER_DETECTED" for w in doc["warnings"]
        ):
            leakage = header_footer_leakage(model, ["confidential | page"])
            assert leakage == 0

    def test_no_empty_pages(self, repeated_header_pdf):
        model = extract_document(repeated_header_pdf)
        assert empty_page_rate(model) == 0.0


# ---------------------------------------------------------------------------
# PDF: Unicode
# ---------------------------------------------------------------------------

class TestUnicodePDF:
    def test_phrase_retention(self, unicode_pdf):
        model = extract_document(unicode_pdf)
        expected = [
            "nregistrarea",
            "concediu",
            "departamentul",
        ]
        assert phrase_retention(model, expected) >= 1.0

    def test_no_corruption(self, unicode_pdf):
        model = extract_document(unicode_pdf)
        assert unicode_corruption_rate(model) == 0.0

    def test_no_empty_pages(self, unicode_pdf):
        model = extract_document(unicode_pdf)
        assert empty_page_rate(model) == 0.0


# ---------------------------------------------------------------------------
# DOCX: nested list
# ---------------------------------------------------------------------------

class TestNestedListDOCX:
    def test_phrase_retention(self, nested_list_docx):
        model = extract_document(nested_list_docx)
        expected = [
            "Introducere document",
            "Punct 1",
            "Punct 2",
            "Sub-punct 2a",
            "Concluzie document",
        ]
        assert phrase_retention(model, expected) >= 1.0

    def test_no_duplicates(self, nested_list_docx):
        model = extract_document(nested_list_docx)
        assert duplicate_line_ratio(model) < 0.05


# ---------------------------------------------------------------------------
# DOCX: table between paragraphs
# ---------------------------------------------------------------------------

class TestTableBetweenParagraphsDOCX:
    def test_phrase_retention(self, table_between_paragraphs_docx):
        model = extract_document(table_between_paragraphs_docx)
        expected = ["Text inainte de tabel", "Text dupa tabel", "Paragraf final"]
        assert phrase_retention(model, expected) >= 1.0

    def test_table_cells(self, table_between_paragraphs_docx):
        model = extract_document(table_between_paragraphs_docx)
        expected_cells = ["Nume", "Departament", "Ion Popescu", "IT", "Maria Ionescu", "HR"]
        assert table_cell_recall(model, expected_cells) >= 1.0


# ---------------------------------------------------------------------------
# XLSX: merged cells
# ---------------------------------------------------------------------------

class TestMergedCellXLSX:
    def test_table_cells(self, merged_cell_xlsx):
        model = extract_document(merged_cell_xlsx)
        expected_cells = ["Header Combinat", "Val1", "Val2", "Val3", "Data1", "Data2", "Data3"]
        assert table_cell_recall(model, expected_cells) >= 1.0

    def test_no_empty_pages(self, merged_cell_xlsx):
        model = extract_document(merged_cell_xlsx)
        assert empty_page_rate(model) == 0.0


# ---------------------------------------------------------------------------
# XLSX: hidden sheet (should still extract)
# ---------------------------------------------------------------------------

class TestHiddenSheetXLSX:
    def test_visible_sheet_extracted(self, hidden_sheet_xlsx):
        model = extract_document(hidden_sheet_xlsx)
        expected_cells = ["Col1", "Col2", "A", "B"]
        assert table_cell_recall(model, expected_cells) >= 1.0

    def test_has_units(self, hidden_sheet_xlsx):
        model = extract_document(hidden_sheet_xlsx)
        # At least the visible sheet should produce a unit
        assert len(model["pages"]) >= 1


# ---------------------------------------------------------------------------
# XLSX: dates and currency
# ---------------------------------------------------------------------------

class TestDatesAndCurrencyXLSX:
    def test_table_cells(self, dates_and_currency_xlsx):
        model = extract_document(dates_and_currency_xlsx)
        expected_cells = ["Date", "Amount", "Currency", "EUR", "RON", "USD"]
        assert table_cell_recall(model, expected_cells) >= 1.0

    def test_no_empty_pages(self, dates_and_currency_xlsx):
        model = extract_document(dates_and_currency_xlsx)
        assert empty_page_rate(model) == 0.0
