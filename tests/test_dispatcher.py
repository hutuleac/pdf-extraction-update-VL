import pytest

from extractor.dispatcher import SUPPORTED_EXTENSIONS, extract_document


def test_supported_extensions():
    assert set(SUPPORTED_EXTENSIONS) == {
        ".pdf", ".csv", ".docx", ".xlsx", ".xlsm", ".txt", ".md",
        ".html", ".htm", ".pptx", ".json", ".xml",
        ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp",
    }


def test_routes_pdf(table_pdf):
    assert extract_document(table_pdf)["document"]["source_type"] == "pdf"


def test_routes_csv(sample_csv):
    assert extract_document(sample_csv)["document"]["source_type"] == "csv"


def test_routes_docx(sample_docx):
    assert extract_document(sample_docx)["document"]["source_type"] == "docx"


def test_routes_xlsx(sample_xlsx):
    assert extract_document(sample_xlsx)["document"]["source_type"] == "xlsx"


def test_routes_xlsm(sample_xlsx, tmp_path):
    xlsm = tmp_path / "macro_workbook.xlsm"
    xlsm.write_bytes(sample_xlsx.read_bytes())
    # AUDIT 12: both extensions share a reader, but a macro workbook reports
    # itself as .xlsm — hardcoding "xlsx" left consumers unable to tell them apart.
    assert extract_document(xlsm)["document"]["source_type"] == "xlsm"


def test_routes_case_insensitive(tmp_path):
    upper = tmp_path / "DATE.CSV"
    upper.write_text("a,b\n1,2\n", encoding="utf-8")
    assert extract_document(upper)["document"]["source_type"] == "csv"


def test_unsupported_raises(tmp_path):
    unsupported = tmp_path / "note.xyz"
    unsupported.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_document(unsupported)
