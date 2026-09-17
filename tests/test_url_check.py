from extractor.url_check import check_page_urls


def test_model_url_confirmed_by_native_text_is_not_flagged():
    blocks = [
        {"type": "text", "content": "See https://example.com/login"},
        {"type": "text", "source": "vlm-figure", "content": "Visit https://example.com/login"},
    ]
    assert check_page_urls(blocks, page_number=1) == []


def test_model_url_with_no_other_source_is_flagged_unverified():
    blocks = [{"type": "text", "source": "vlm", "content": "https://fabricated.example/x"}]
    warnings = check_page_urls(blocks, page_number=3)
    assert warnings == [{
        "code": "VLM_URL_UNVERIFIED",
        "page": 3,
        "model_url": "https://fabricated.example/x",
    }]


def test_model_url_close_to_a_trusted_url_names_it():
    # The real-world case this exists for: OCR misreads "login" as "loqin",
    # the model reads it correctly. Flag the disagreement either direction.
    blocks = [
        {"type": "text", "source": "ocr", "content": "https://acme.example/loqin"},
        {"type": "text", "source": "vlm-figure", "content": "https://acme.example/login"},
    ]
    warnings = check_page_urls(blocks, page_number=5)
    assert warnings == [{
        "code": "VLM_URL_UNVERIFIED",
        "page": 5,
        "model_url": "https://acme.example/login",
        "other_url": "https://acme.example/loqin",
    }]


def test_non_text_blocks_are_ignored():
    blocks = [{"type": "table", "content": [["https://example.com"]]}]
    assert check_page_urls(blocks, page_number=1) == []


if __name__ == "__main__":
    # ponytail self-check: run without pytest, e.g. `python tests/test_url_check.py`
    test_model_url_confirmed_by_native_text_is_not_flagged()
    test_model_url_with_no_other_source_is_flagged_unverified()
    test_model_url_close_to_a_trusted_url_names_it()
    test_non_text_blocks_are_ignored()
    print("ok")
