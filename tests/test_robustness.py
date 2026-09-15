import json

import pytest
from docx import Document

from extractor.dispatcher import extract_document
from extractor.errors import ExtractionError, ResourceLimitError
from extractor.json_reader import extract_json
from extractor.limits import MAX_JSON_DEPTH
from extractor.xml_reader import extract_xml
from main import main


def test_json_depth_breach_is_resource_limit(tmp_path):
    path = tmp_path / "deep.json"
    data = None
    for _ in range(MAX_JSON_DEPTH + 2):
        data = {"x": data}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ExtractionError) as raised:
        extract_json(path)
    assert raised.value.category.value == "resource_limit"


def test_json_container_breach_is_resource_limit(tmp_path):
    path = tmp_path / "many.json"
    path.write_text(json.dumps({"items": list(range(10))}), encoding="utf-8")
    with pytest.raises(ResourceLimitError):
        extract_json(path, max_containers=1)


def test_xml_depth_breach_is_resource_limit(tmp_path):
    path = tmp_path / "deep.xml"
    path.write_text("<x>" * 5 + "text" + "</x>" * 5, encoding="utf-8")
    with pytest.raises(ResourceLimitError):
        extract_xml(path, max_depth=3)


def test_malformed_pdf_does_not_stop_valid_file(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "broken.pdf").write_bytes(b"not a pdf")
    (input_dir / "valid.txt").write_text("valid text", encoding="utf-8")
    output_dir = tmp_path / "output"

    assert main([
        "--input", str(input_dir),
        "--output", str(output_dir),
        "--log-file", str(tmp_path / "run.log"),
    ]) == 1
    assert (output_dir / "json" / "valid.json").exists()
    assert not (output_dir / "json" / "broken.json").exists()


def test_mutated_docx_is_typed_or_valid(tmp_path):
    original = tmp_path / "original.docx"
    document = Document()
    document.add_paragraph("content")
    document.save(original)
    data = bytearray(original.read_bytes())
    mutated = tmp_path / "mutated.docx"
    mutated.write_bytes(data[:100] + bytes([data[100] ^ 1]) + data[101:])

    try:
        model = extract_document(mutated)
    except ExtractionError as exc:
        assert exc.category.value in {"malformed", "missing_part", "io"}
    else:
        assert model["schema_version"] == "2.0"
        assert model["pages"]
