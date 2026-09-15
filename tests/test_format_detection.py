from zipfile import ZipFile

import pytest

from extractor.dispatcher import extract_document
from extractor.errors import ExtractionError


def test_pdf_extension_with_non_pdf_bytes_is_malformed(tmp_path):
    path = tmp_path / "wrong.pdf"
    path.write_bytes(b"plain text, not a PDF")
    with pytest.raises(ExtractionError) as raised:
        extract_document(path)
    assert raised.value.category.value == "malformed"


def test_txt_keeps_extension_fallback(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("%PDF-1.7 is ordinary text here", encoding="utf-8")
    model = extract_document(path)
    assert model["document"]["source_type"] == "txt"


def test_ooxml_package_without_content_types_reports_missing_part(tmp_path):
    path = tmp_path / "broken.docx"
    with ZipFile(path, "w") as package:
        package.writestr("word/document.xml", "<document />")
    with pytest.raises(ExtractionError) as raised:
        extract_document(path)
    assert raised.value.category.value == "missing_part"
