from extractor.docx_reader import extract_docx


def test_extract_docx_shape(sample_docx):
    model = extract_docx(sample_docx)
    assert model["document"]["source_type"] == "docx"
    assert model["document"]["filename"] == "raport_test.docx"
    assert model["document"]["pages"] == 1
    unit = model["pages"][0]
    assert unit["unit"] == 1
    assert unit["unit_type"] == "section"


def test_extract_docx_blocks_and_order(sample_docx):
    blocks = extract_docx(sample_docx)["pages"][0]["content"]
    types = [b["type"] for b in blocks]
    # paragraph -> text, then table, then paragraph -> text
    assert types == ["text", "table", "text"]
    assert "Procedura de concedii" in blocks[0]["content"]
    assert blocks[1]["content"] == [["Rol", "Actiune"], ["Manager", "Aproba"]]
    assert "A doua sectiune" in blocks[2]["content"]
