from extractor.ocr.config import DEFAULT_MAX_PIXELS, reset


def test_default_ocr_pixel_budget_is_25_megapixels():
    reset()
    assert DEFAULT_MAX_PIXELS == 25_000_000
