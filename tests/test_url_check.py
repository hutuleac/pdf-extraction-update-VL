from extractor.url_check import drop_model_urls


def test_model_urls_are_dropped_and_counted():
    blocks = [{"type": "text", "source": "vlm", "content": "Visit https://fabricated.example/x  or www.b.example today"}]
    assert drop_model_urls(blocks, page_number=3) == [
        {"code": "VLM_URLS_DROPPED", "page": 3, "count": 2},
    ]
    assert blocks[0]["content"] == "Visit or today"


def test_native_and_ocr_urls_are_kept():
    blocks = [
        {"type": "text", "content": "See https://example.com/login"},
        {"type": "text", "source": "ocr", "content": "https://acme.example/login"},
    ]
    assert drop_model_urls(blocks, page_number=1) == []
    assert blocks[0]["content"] == "See https://example.com/login"
    assert blocks[1]["content"] == "https://acme.example/login"


def test_model_table_cells_are_stripped_and_empty_text_removed():
    blocks = [
        {"type": "text", "source": "vlm", "content": "https://only.example"},
        {"type": "table", "source": "vlm", "content": [["Site", "https://x.example"]]},
        {"type": "table", "content": [["https://native.example"]]},
    ]
    assert drop_model_urls(blocks, page_number=2)[0]["count"] == 2
    assert blocks == [
        {"type": "table", "source": "vlm", "content": [["Site", ""]]},
        {"type": "table", "content": [["https://native.example"]]},
    ]
