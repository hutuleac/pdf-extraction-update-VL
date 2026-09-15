"""Unit tests for side-margin label and whole-document repeated-line
detection (top/bottom bands are covered indirectly through the golden corpus
and baseline snapshot)."""
from extractor.headers_footers import (
    detect_headers_footers,
    detect_repeated_body_lines,
    remove_repeated_lines_from_text,
)

PAGE_HEIGHT = 800.0
PAGE_WIDTH = 600.0


def _word(x0, y0, x1, y1, text, block=0, line=0, word_no=0):
    return (x0, y0, x1, y1, text, block, line, word_no)


def _page_words(*, side_label: str | None, body_line: str) -> list[tuple]:
    words = [
        _word(200, 400, 400, 415, w, block=1, line=0, word_no=i)
        for i, w in enumerate(body_line.split())
    ]
    if side_label:
        words.append(_word(5, 300, 30, 315, side_label, block=2, line=0))
    return words


def test_repeated_side_label_is_detected_and_kept_out_of_headers_footers():
    num_pages = 5
    page_texts = [""] * num_pages
    page_heights = [PAGE_HEIGHT] * num_pages
    page_widths = [PAGE_WIDTH] * num_pages
    page_words = [
        _page_words(side_label="Stabilitatea", body_line="Some real sentence here")
        for _ in range(num_pages)
    ]

    result = detect_headers_footers(page_texts, page_heights, page_words, page_widths)

    assert result.side_lines_by_page[1] == ["Stabilitatea"]
    assert result.headers_by_page == {}
    assert result.footers_by_page == {}
    assert result.detected is True


def test_one_off_left_margin_word_is_not_flagged_as_side_label():
    num_pages = 5
    page_texts = [""] * num_pages
    page_heights = [PAGE_HEIGHT] * num_pages
    page_widths = [PAGE_WIDTH] * num_pages
    page_words = [_page_words(side_label=None, body_line="Some real sentence here")] * (
        num_pages - 1
    ) + [_page_words(side_label="Note", body_line="Some real sentence here")]

    result = detect_headers_footers(page_texts, page_heights, page_words, page_widths)

    assert result.side_lines_by_page == {}


def test_no_page_widths_skips_side_band_detection():
    num_pages = 5
    page_texts = [""] * num_pages
    page_heights = [PAGE_HEIGHT] * num_pages
    page_words = [
        _page_words(side_label="Stabilitatea", body_line="Some real sentence here")
        for _ in range(num_pages)
    ]

    result = detect_headers_footers(page_texts, page_heights, page_words)

    assert result.side_lines_by_page == {}


def test_remove_side_lines_drops_every_occurrence():
    text = "Stabilitatea\nReal sentence one.\nStabilitatea\nReal sentence two."

    cleaned = remove_repeated_lines_from_text(text, ["Stabilitatea"])

    assert cleaned == "Real sentence one.\nReal sentence two."


def test_remove_side_lines_noop_when_no_lines():
    assert remove_repeated_lines_from_text("a\nb", []) == "a\nb"


def test_body_line_repeated_on_a_fifth_of_pages_is_detected():
    # 42-44% on the real 388-page document that surfaced this; a small
    # synthetic doc uses the same shape, well above both the 20% floor and
    # the 20-page minimum (below which a short document's own filler text
    # can legitimately repeat verbatim and shouldn't be mistaken for chrome).
    title = "GEOTEHNICA - note de curs, chapter title line"
    num_pages = 20
    page_texts = [
        f"{title}\nSome unrelated real sentence for page {i}."
        if i % 2 == 0 else f"Some unrelated real sentence for page {i}."
        for i in range(num_pages)
    ]

    result = detect_repeated_body_lines(page_texts)

    assert result[1] == [title]
    assert 2 not in result  # page 2 (i=1, odd) never carried the title


def test_below_min_length_line_is_never_flagged_as_chrome():
    # "1" repeats on every page but is below MIN_CHROME_LINE_LENGTH — a lone
    # page number or bullet marker must never be mass-stripped.
    page_texts = ["1\nSome real sentence."] * 20

    result = detect_repeated_body_lines(page_texts)

    assert all("1" not in lines for lines in result.values())


def test_numbered_sentence_is_not_collapsed_across_pages():
    # Digits stay literal here (unlike header/footer band matching): a real
    # sentence that only varies by number must not be treated as one chrome
    # line repeated on every page.
    page_texts = [f"Section {i} covers a distinct real topic in depth." for i in range(20)]

    result = detect_repeated_body_lines(page_texts)

    assert result == {}


def test_line_seen_on_a_single_page_is_kept():
    # Appearing once, out of 20 pages, is nowhere near the repetition
    # threshold — a real one-off heading must survive untouched.
    title = "A title that is long enough to qualify by length alone"
    page_texts = [f"{title}\nBody text."] + ["Body text only, no title here."] * 19

    result = detect_repeated_body_lines(page_texts)

    assert all(title not in lines for lines in result.values())


def test_short_document_is_exempt_even_with_universal_repeat():
    # Below MIN_PAGES_FOR_REPEATED_LINE_DETECTION: a short document's own
    # deliberately-repeated filler sentence must not be mistaken for chrome.
    title = "A title that is long enough to qualify by length alone"
    page_texts = [f"{title}\nBody text."] * 10

    result = detect_repeated_body_lines(page_texts)

    assert result == {}
