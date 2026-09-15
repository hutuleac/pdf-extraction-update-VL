"""Repeated header/footer detection for PDF pages.

Strategy:
  1. For each page, collect candidate lines from the top band and bottom band
     (a configurable fraction of page height, default 12%).
  2. Normalise each line into a "signature" by replacing digits with '#' and
     collapsing whitespace/punctuation — so "Page 1", "Page 2", "Page 37" all
     map to the same signature.
  3. Count how many pages each signature appears on.
  4. Signatures appearing on more than a configurable fraction of pages (default
     60%, minimum 3 pages) are labelled as headers or footers.

Documents under 3 pages are left untouched — not enough repetition to measure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Fraction of page height considered "top band" and "bottom band"
BAND_FRACTION = 0.12

# Fraction of page width considered "left band" and "right band" — catches a
# slide-deck's side-margin chapter/title strip, which top/bottom bands can't
# see. A side label sits inside the column of body text (not off in its own
# band the way a top/bottom banner is), so reading_order folds it straight
# into the flow instead of leaving it isolated — it can turn up anywhere in a
# page's text, sometimes more than once per page (the same label duplicated in
# more than one slide placeholder). Unlike a top/bottom banner, that repeat
# count makes it safe to strip every matching line, not just first/last.
SIDE_BAND_FRACTION = 0.15

# Minimum fraction of pages a signature must appear on to be considered repeated
MIN_OCCURRENCE_FRACTION = 0.60

# A slide-deck title or author byline can recur inline within the body flow —
# not confined to any edge band, so the top/bottom/side checks above can't
# localize it — often enough to dominate the document but too rarely to clear
# their 60% bar (measured at 42-44% of pages on a 388-page course, one slide
# per "chapter start" rather than every slide). A lower bar is safe here
# because the signal is stronger: an exact whole-line match across the entire
# page, not just words inside a narrow edge band. Real prose does not repeat
# verbatim — punctuation and all — across two in five pages of a document.
REPEATED_LINE_MIN_FRACTION = 0.20
# Below this length a repeated short line ("da.", "•", a lone page number) is
# common by chance in ordinary prose; dropping every instance would erase real
# content. A title or byline clears this by a wide margin.
MIN_CHROME_LINE_LENGTH = 12
# A short document can legitimately reuse one identical sentence as its own
# filler content on most of its pages (measured directly: three fixture PDFs
# do exactly this, each under 10 pages) with no way to tell that apart from
# real chrome by text alone — the discriminating signal only becomes reliable
# once "most pages" means dozens of pages, not three. Mirrors the same
# reasoning as `raster.REPEATED_IMAGE_MIN_PAGES` for repeated images.
MIN_PAGES_FOR_REPEATED_LINE_DETECTION = 20

# Absolute minimum page count before we attempt detection
MIN_PAGES_FOR_DETECTION = 3


# ---------------------------------------------------------------------------
# Signature normalisation
# ---------------------------------------------------------------------------

_DIGITS = re.compile(r"\d+")
_MULTISPACE = re.compile(r"\s+")


def _make_signature(line: str) -> str:
    """Normalise a line into a repeatable signature.

    Digits become '#', whitespace collapses, case is lowered.
    """
    sig = _DIGITS.sub("#", line)
    sig = _MULTISPACE.sub(" ", sig)
    return sig.strip().lower()


def _make_exact_signature(line: str) -> str:
    """Normalise a line for exact-text repetition, keeping digits literal.

    Used where digit-blind matching (`_make_signature`) would be wrong: a
    numbered body sentence ("Section 1 covers...", "Section 2 covers...")
    must stay distinct, unlike a page-number footer where "Page 1"/"Page 2"
    are meant to collapse. Chrome text (a title, a byline) repeats character
    for character, so this only needs to fold whitespace and case.
    """
    sig = _MULTISPACE.sub(" ", line)
    return sig.strip().lower()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DetectedHeaderFooter:
    """A repeated header or footer found across multiple pages."""

    kind: str  # "header" or "footer"
    signature: str
    original_lines: dict[int, str] = field(default_factory=dict)
    # Maps 1-based page_number -> the actual line text on that page


@dataclass
class HeaderFooterResult:
    """Result of header/footer detection across a document."""

    # Per-page: lines identified as headers (top band)
    headers_by_page: dict[int, list[str]] = field(default_factory=dict)
    # Per-page: lines identified as footers (bottom band)
    footers_by_page: dict[int, list[str]] = field(default_factory=dict)
    # Per-page: lines identified as a side-margin label (left or right band)
    side_lines_by_page: dict[int, list[str]] = field(default_factory=dict)
    # Whether any detection occurred
    detected: bool = False


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def _extract_band_lines(
    page_height: float,
    page_width: float,
    word_data: list[tuple],
    band: str,
) -> list[str]:
    """Extract lines from one edge band of a page using word positions.

    *word_data* is the result of page.get_text("words"): list of
    (x0, y0, x1, y1, word, block_no, line_no, word_no).

    Returns deduplicated lines (joined words) in reading order.
    """
    if band == "top":
        threshold = page_height * BAND_FRACTION
        band_words = [w for w in word_data if w[1] < threshold]
    elif band == "bottom":
        cutoff = page_height - page_height * BAND_FRACTION
        band_words = [w for w in word_data if w[3] > cutoff]
    elif band == "left":
        threshold = page_width * SIDE_BAND_FRACTION
        band_words = [w for w in word_data if w[2] < threshold]
    else:  # right
        cutoff = page_width - page_width * SIDE_BAND_FRACTION
        band_words = [w for w in word_data if w[0] > cutoff]

    if not band_words:
        return []

    # Group words by (block_no, line_no) to reconstruct lines
    lines_map: dict[tuple[int, int], list[tuple[float, str]]] = {}
    for x0, _y0, _x1, _y1, word, block_no, line_no, _word_no in band_words:
        key = (block_no, line_no)
        lines_map.setdefault(key, []).append((x0, word))

    # Sort lines by block then line number, words by x position
    result: list[str] = []
    for key in sorted(lines_map):
        words_sorted = sorted(lines_map[key], key=lambda t: t[0])
        line_text = " ".join(w for _, w in words_sorted)
        if line_text.strip():
            result.append(line_text.strip())

    return result


def detect_headers_footers(
    page_texts: list[str],
    page_heights: list[float],
    page_words: list[list[tuple]],
    page_widths: list[float] | None = None,
) -> HeaderFooterResult:
    """Detect repeated headers, footers, and side-margin labels across all pages.

    Parameters:
      page_texts: raw text per page (not used directly, kept for API symmetry)
      page_heights: height of each page in points
      page_words: per-page word data from page.get_text("words")
      page_widths: width of each page in points; side-band detection is
        skipped (as if absent) when not given

    Returns a HeaderFooterResult with per-page header/footer/side lines.
    """
    num_pages = len(page_texts)

    if num_pages < MIN_PAGES_FOR_DETECTION:
        return HeaderFooterResult()

    min_occurrences = max(
        MIN_PAGES_FOR_DETECTION,
        int(num_pages * MIN_OCCURRENCE_FRACTION),
    )

    # Collect candidate signatures per band. signature -> {page_number: line}
    candidates: dict[str, dict[str, dict[int, str]]] = {
        "top": {}, "bottom": {}, "left": {}, "right": {},
    }

    for page_idx in range(num_pages):
        page_number = page_idx + 1
        height = page_heights[page_idx]
        width = page_widths[page_idx] if page_widths else 0
        words = page_words[page_idx]

        bands = ("top", "bottom", "left", "right") if page_widths else ("top", "bottom")
        for band in bands:
            for line in _extract_band_lines(height, width, words, band):
                sig = _make_signature(line)
                if sig:
                    candidates[band].setdefault(sig, {})[page_number] = line

    def _repeated_lines(band: str) -> dict[int, list[str]]:
        by_page: dict[int, list[str]] = {}
        for _sig, pages_map in candidates[band].items():
            if len(pages_map) >= min_occurrences:
                for page_number, line in pages_map.items():
                    by_page.setdefault(page_number, []).append(line)
        return by_page

    headers_by_page = _repeated_lines("top")
    footers_by_page = _repeated_lines("bottom")
    side_by_page: dict[int, list[str]] = {}
    for band in ("left", "right"):
        for page_number, lines in _repeated_lines(band).items():
            side_by_page.setdefault(page_number, []).extend(lines)

    detected = bool(headers_by_page or footers_by_page or side_by_page)
    return HeaderFooterResult(
        headers_by_page=headers_by_page,
        footers_by_page=footers_by_page,
        side_lines_by_page=side_by_page,
        detected=detected,
    )


def detect_repeated_body_lines(page_texts: list[str]) -> dict[int, list[str]]:
    """Detect exact lines that repeat, verbatim, across a large share of pages
    regardless of where on the page they sit.

    Complements `detect_headers_footers`: a slide template can re-insert a
    title or byline into the body flow of most slides at no fixed position,
    so no edge band ever isolates it. Position doesn't matter here — document-
    wide repetition of the exact same line is itself the evidence.

    Returns {page_number: [line, ...]} for pages carrying such a line.
    """
    num_pages = len(page_texts)
    if num_pages < MIN_PAGES_FOR_REPEATED_LINE_DETECTION:
        return {}

    min_occurrences = max(
        MIN_PAGES_FOR_DETECTION,
        int(num_pages * REPEATED_LINE_MIN_FRACTION),
    )

    # signature -> {page_number: line}. One entry per page per signature even
    # if a line repeats several times on that page — a page voting many times
    # for its own signature would let a locally-duplicated real sentence look
    # document-wide repeated when it never left that one page.
    candidates: dict[str, dict[int, str]] = {}
    for page_idx, text in enumerate(page_texts):
        page_number = page_idx + 1
        seen_this_page: set[str] = set()
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if len(line) < MIN_CHROME_LINE_LENGTH:
                continue
            sig = _make_exact_signature(line)
            if not sig or sig in seen_this_page:
                continue
            seen_this_page.add(sig)
            candidates.setdefault(sig, {})[page_number] = line

    by_page: dict[int, list[str]] = {}
    for _sig, pages_map in candidates.items():
        if len(pages_map) >= min_occurrences:
            for page_number, line in pages_map.items():
                by_page.setdefault(page_number, []).append(line)
    return by_page


def remove_lines_from_text(
    text: str,
    header_lines: list[str],
    footer_lines: list[str] | None = None,
) -> str:
    """Remove each detected banner line from a page's text, once.

    Matches are exact (after stripping whitespace from both sides). Each header
    drops its *first* occurrence and each footer its *last* — a banner appears
    once on a page, so removing every match would strip a real body heading
    that happens to equal it. A document whose header is a plain word
    ('SPECIFICATION', 'Summary', a product name) previously lost that word
    everywhere it legitimately appeared.

    Position alone cannot decide this: the text arrives in column-aware reading
    order, so a footer is not necessarily the last line. Which band a line came
    from is what the caller knows, so that is what it passes.
    """
    footer_lines = footer_lines or []
    if not header_lines and not footer_lines:
        return text

    lines = text.splitlines()
    stripped = [line.strip() for line in lines]
    drop: set[int] = set()

    def drop_one(target: str, indices) -> None:
        for i in indices:
            if i not in drop and stripped[i] == target.strip():
                drop.add(i)
                return

    forward = range(len(lines))
    backward = range(len(lines) - 1, -1, -1)
    for line in header_lines:
        drop_one(line, forward)
    for line in footer_lines:
        drop_one(line, backward)

    return "\n".join(line for i, line in enumerate(lines) if i not in drop)


def remove_repeated_lines_from_text(text: str, chrome_lines: list[str]) -> str:
    """Remove every occurrence of each detected chrome line.

    Used for both a side-margin label and a whole-document repeated body line
    (see `detect_repeated_body_lines`): both are decorative chrome proven by
    document-wide repetition, not body content that could coincidentally
    repeat a heading, and reading order can fold the same line into a page's
    text more than once — so unlike `remove_lines_from_text`, every match is
    dropped, not just one.
    """
    if not chrome_lines:
        return text

    targets = {line.strip() for line in chrome_lines}
    lines = text.splitlines()
    return "\n".join(line for line in lines if line.strip() not in targets)
