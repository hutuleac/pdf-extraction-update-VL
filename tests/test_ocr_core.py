"""Unit tests for the OCR building blocks — no model files needed.

Only numpy is required, so these run on any machine that can import the OCR
extra, with or without the .onnx weights present.
"""
import pytest

np = pytest.importorskip("numpy")

from extractor.ocr.base import (  # noqa: E402
    OcrLine,
    OcrResult,
    OcrUnavailable,
    UnavailableReason,
)
from extractor.ocr.config import DEFAULT_DPI, configure, get_config  # noqa: E402
from extractor.ocr.ctc import clean_icon_noise, decode_greedy, is_isolated_cjk_glyph  # noqa: E402
from extractor.ocr.paths import (  # noqa: E402
    DICT_PATH,
    candidate_dirs,
    load_labels,
    resolve_models,
)
from extractor.warning_text import describe  # noqa: E402

LABELS = ["<blank>", "a", "b", "c", " "]


def _one_hot(sequence: list[int], classes: int = len(LABELS)) -> np.ndarray:
    """Build a (T, C) probability array that is certain about each step."""
    array = np.zeros((len(sequence), classes), dtype=np.float32)
    for step, index in enumerate(sequence):
        array[step, index] = 1.0
    return array


# ---------------------------------------------------------------------------
# CTC decoding
# ---------------------------------------------------------------------------

def test_decode_collapses_repeats_and_drops_blanks():
    # The blank at step 2 is what separates the two 'a's; without it the
    # repeated class would collapse into one character.
    text, confidence = decode_greedy(_one_hot([1, 1, 0, 1, 2, 0, 3]), LABELS)
    assert text == "aabc"
    assert confidence == pytest.approx(1.0)


def test_decode_empty_input_is_empty_not_an_error():
    assert decode_greedy(_one_hot([0, 0, 0]), LABELS) == ("", 0.0)


def test_decode_applies_softmax_to_raw_logits():
    logits = np.array([[-5.0, 8.0, -5.0, -5.0, -5.0]], dtype=np.float32)
    text, confidence = decode_greedy(logits, LABELS)
    assert text == "a"
    assert 0.0 < confidence <= 1.0


def test_decode_refuses_a_class_count_mismatch():
    with pytest.raises(ValueError, match="dictionary"):
        decode_greedy(_one_hot([1], classes=7), LABELS)


def test_decode_confidence_is_the_mean_of_kept_characters():
    probs = np.array([
        [0.0, 1.0, 0.0, 0.0, 0.0],
        [0.4, 0.0, 0.6, 0.0, 0.0],
    ], dtype=np.float32)
    _, confidence = decode_greedy(probs, LABELS)
    assert confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Icon-glyph noise (a Chinese-capable model misreading UI icons as CJK)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("glyph", ["四", "人", "亩"])  # 四, 人, 亩
def test_isolated_cjk_glyph_is_detected(glyph):
    assert is_isolated_cjk_glyph(glyph)


@pytest.mark.parametrize("text", [
    "",
    "A",
    "Sort",
    "中国",  # 中国 -- two CJK characters, likely genuine text, not an icon
])
def test_non_icon_text_is_not_flagged(text):
    assert not is_isolated_cjk_glyph(text)


def test_clean_icon_noise_replaces_a_lone_glyph():
    assert clean_icon_noise("四") == "[icon]"


def test_clean_icon_noise_leaves_real_text_alone():
    assert clean_icon_noise("Sort") == "Sort"
    assert clean_icon_noise("中国") == "中国"


# ---------------------------------------------------------------------------
# Character dictionary
# ---------------------------------------------------------------------------

def test_vendored_dictionary_matches_the_model_class_count():
    # blank + 18,708 entries + space is exactly what PP-OCRv6_small emits.
    assert len(load_labels(DICT_PATH)) == 18_710


def test_labels_start_with_blank_and_end_with_space():
    labels = load_labels(DICT_PATH)
    assert labels[0] == "<blank>"
    assert labels[-1] == " "


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def test_explicit_directory_is_searched_first():
    assert candidate_dirs("X:/somewhere")[0].as_posix() == "X:/somewhere"


def test_missing_model_directory_reports_where_it_looked():
    with pytest.raises(OcrUnavailable) as error:
        resolve_models("X:/definitely/not/here/at/all")
    assert error.value.reason is UnavailableReason.MISSING_MODEL_DIR
    assert "looked in" in error.value.detail


def test_a_directory_with_only_one_model_names_the_missing_one(tmp_path):
    (tmp_path / "some_det_infer.onnx").write_bytes(b"0" * 200_000)
    with pytest.raises(OcrUnavailable) as error:
        resolve_models(str(tmp_path))
    assert error.value.reason is UnavailableReason.MISSING_REC_MODEL


def test_symlink_stubs_are_not_mistaken_for_models(tmp_path):
    # The huggingface_hub cache copied from macOS leaves 1 KB XSym stubs behind.
    (tmp_path / "stub_det_infer.onnx").write_bytes(b"XSym" + b"0" * 1000)
    (tmp_path / "stub_rec_infer.onnx").write_bytes(b"XSym" + b"0" * 1000)
    with pytest.raises(OcrUnavailable):
        resolve_models(str(tmp_path))


# ---------------------------------------------------------------------------
# Results, configuration and wording
# ---------------------------------------------------------------------------

def test_result_joins_lines_and_reports_each_score():
    result = OcrResult(
        lines=[
            OcrLine("first", 0.9, (0, 0, 1, 1)),
            OcrLine("second", 0.7, (0, 1, 1, 2)),
            OcrLine("", 0.1, (0, 2, 1, 3)),
        ],
        engine="onnx",
    )
    assert result.text() == "first\nsecond"
    assert result.scores() == pytest.approx([0.9, 0.7])


def test_empty_result_has_no_scores_not_a_crash():
    assert OcrResult(lines=[], engine="onnx").scores() == []


def test_configure_changes_settings_and_reset_restores_them():
    from extractor.ocr import config

    configure(dpi=150, enabled=False)
    assert get_config().dpi == 150
    assert get_config().enabled is False
    config.reset()
    assert get_config().dpi == DEFAULT_DPI
    assert get_config().enabled is True


def test_warnings_are_described_in_plain_language():
    assert describe({"code": "SCANNED_PAGE_NO_TEXT", "page": 4}).startswith("Page 4: scanned page")
    assert "94%" in describe({"code": "OCR_APPLIED", "page": 1, "confidence": 0.94})
    assert "no OCR model" in describe(
        {"code": "OCR_UNAVAILABLE", "detail": "no OCR model directory was found"}
    )


def test_mixed_confidence_warning_names_how_many_lines_are_weak():
    text = describe(
        {"code": "OCR_MIXED_CONFIDENCE", "page": 7, "low_lines": 2, "total_lines": 9,
         "lowest": 0.21}
    )
    assert text.startswith("Page 7: ")
    assert "2 of 9" in text
    assert "21%" in text


def test_noise_filtered_warning_quotes_what_was_discarded():
    """The reader has to be able to judge whether the filter overreached."""
    text = describe(
        {"code": "OCR_NOISE_FILTERED", "page": 3, "dropped": 2, "sample": ["ω", "e"]}
    )
    assert text.startswith("Page 3: ")
    assert "2" in text
    assert "ω" in text and "e" in text


def test_unknown_warning_code_is_still_visible():
    assert describe({"code": "SOMETHING_NEW"}) == "SOMETHING_NEW"
