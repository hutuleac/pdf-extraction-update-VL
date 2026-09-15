from extractor.csv_reader import extract_csv


def test_extract_csv_shape(sample_csv):
    model = extract_csv(sample_csv)
    assert model["document"]["source_type"] == "csv"
    assert model["document"]["filename"] == "date_test.csv"
    assert model["document"]["pages"] == 1
    unit = model["pages"][0]
    assert unit["unit"] == 1
    assert unit["unit_type"] == "section"


def test_extract_csv_table_content(sample_csv):
    model = extract_csv(sample_csv)
    blocks = model["pages"][0]["content"]
    tables = [b for b in blocks if b["type"] == "table"]
    assert len(tables) == 1
    assert tables[0]["content"] == [
        ["Rol", "Actiune"],
        ["Manager", "Aproba"],
        ["HR", "Inregistreaza"],
    ]


def test_extract_csv_empty(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    model = extract_csv(empty)
    assert model["pages"][0]["content"] == []
