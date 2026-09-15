import json

from extractor.json_writer import write_json

MODEL = {
    "document": {"filename": "doc.pdf", "pages": 1, "author": "", "title": "",
                 "has_images": False, "image_count": 0},
    "pages": [
        {"page": 1, "has_images": False, "image_count": 0,
         "content": [
             {"type": "text", "content": "Salut lume"},
             {"type": "table", "content": [["Rol", "Actiune"], ["Manager", "Aproba"]]},
         ]},
    ],
}


def test_write_json_creates_file_and_roundtrips(tmp_path):
    out = write_json(MODEL, tmp_path)
    assert out.name == "doc.json"
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded == MODEL


def test_write_json_preserves_unicode(tmp_path):
    model = json.loads(json.dumps(MODEL))
    model["pages"][0]["content"][0]["content"] = "aprobă cererea"
    out = write_json(model, tmp_path)
    assert "aprobă" in out.read_text(encoding="utf-8")  # not \u escaped
