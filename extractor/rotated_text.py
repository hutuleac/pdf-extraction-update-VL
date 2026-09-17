"""Detect stamped rotated text (watermarks) in a PDF's native text layer.

A stamp such as "printed by X on DATE" sits in the text layer at an angle.
Both PyMuPDF and pdfplumber order text by position, so its glyphs land
*between* body characters and corrupt words from the inside — "pas6sengers",
"cKonditions", a table cell holding a lone "K". The page image looks perfect;
only the extracted text is damaged, which is what makes it easy to miss.

The signal that separates a stamp from real content is the text direction:

  upright       body text
  axis-rotated  90/270 deg — legitimate content: sideways table headers, chart
                axis labels, the author column on a title page
  skewed        neither — a diagonal stamp

Only *skewed* text is dropped. Measured across three documents: 8,085 skewed
characters in a 105-page specification, all one stamp; zero in a 71-page
standard; zero in a 13-page slide deck, whose 110 axis-rotated characters were
real diagram labels and were kept.

pdfplumber's own ``upright`` flag cannot be used here — it reports True for a
52 deg stamp and False for real 90 deg content, exactly backwards.

A recurrence test ("only drop skewed text repeating across most pages") was
built and measured, then dropped: it changed no output on any of the three
documents, because the three-way split above is what spares real content, and
it cost a document-wide pre-scan (+39% to +114% on the pdfplumber pass). The
caller reports every drop instead, so a genuine rotated label is surfaced
rather than lost silently.

A fourth document later showed the gap that split leaves open: a margin stamp
set at exactly 90 deg (vertical), not skewed, so it reads as "legitimate
axis-rotated content" and survives into every table it crosses. Angle alone
cannot tell it apart from a real sideways header — only repetition can, so
``repeated_vertical_boxes`` adds that one narrow check: vertical text density
here is low (real 90 deg content is rare to begin with), so the pre-scan cost
that sank the earlier general recurrence test does not apply.
"""
from __future__ import annotations

# A direction component below this counts as zero. Generous on purpose: real
# axis-aligned text is exact, and a stamp is tens of degrees off, so nothing
# legitimate sits near the boundary.
EPSILON = 0.01

# Two boxes describe the same word above this intersection-over-union. Only
# rounding separates a stamp word rebuilt from its characters and the same word
# as PyMuPDF reports it, so the bar is high; a body word the stamp merely
# crosses scores far below it.
MATCH_IOU = 0.8

# A vertical (axis-rotated) word must repeat on at least this many pages
# before it counts as a margin stamp rather than a genuine one-off rotated
# label (a sideways table header, a spine-style figure caption). See
# repeated_vertical_boxes.
STAMP_MIN_PAGES = 5


def is_skewed(dx: float, dy: float) -> bool:
    """True if a text direction is neither horizontal nor vertical.

    Takes the writing direction: ``(a, b)`` from a pdfplumber char matrix, or
    ``line["dir"]`` from PyMuPDF. Both components are non-zero only when the
    text runs at an angle.
    """
    return abs(dx) >= EPSILON and abs(dy) >= EPSILON


def keep_char(obj) -> bool:
    """pdfplumber ``page.filter`` predicate: drop skewed glyphs, keep the rest.

    Non-char objects (lines, rects, curves) are always kept — the ruling lines
    a table is detected from must survive the filter.
    """
    if obj.get("object_type") != "char":
        return True
    matrix = obj.get("matrix")
    return matrix is None or not is_skewed(matrix[0], matrix[1])


def _lines_where(page, predicate):
    """Yield the lines of a PyMuPDF page whose direction satisfies *predicate*."""
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:  # 0 == text; images have no direction
            continue
        for line in block.get("lines", ()):
            if predicate(*line["dir"]):
                yield line


def _union(boxes) -> tuple:
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def _iou(a, b) -> float:
    """Intersection over union of two boxes; 0.0 when they do not meet."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    overlap = width * height
    union = (
        (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    )
    return overlap / union if union > 0 else 0.0


def _words_where(page, predicate) -> list[tuple[tuple, str]]:
    """Group the characters of the lines matching *predicate* into
    ``(bbox, text)`` words.

    PyMuPDF splits words on whitespace, so grouping the characters the same
    way reproduces exactly the boxes ``get_text("words")`` reports — which is
    what makes matching them reliable.
    """
    words: list[tuple[tuple, str]] = []
    for line in _lines_where(page, predicate):
        current: list[tuple] = []
        text: list[str] = []
        for span in line.get("spans", ()):
            for char in span.get("chars", ()):
                if char["c"].isspace():
                    if current:
                        words.append((_union(current), "".join(text)))
                        current, text = [], []
                else:
                    current.append(tuple(char["bbox"]))
                    text.append(char["c"])
        if current:
            words.append((_union(current), "".join(text)))
    return words


def skewed_words_and_text(page) -> tuple[list[tuple], str]:
    """Return the stamp's word boxes and the text they spell."""
    words = _words_where(page, is_skewed)
    return [box for box, _ in words], " ".join(text for _, text in words)


def repeated_vertical_words(doc, *, min_pages: int = STAMP_MIN_PAGES) -> dict[int, list[tuple[tuple, str]]]:
    """PyMuPDF twin of ``repeated_vertical_boxes`` for the text pass.

    Returns ``{page_number: [(bbox, text), ...]}`` for vertical words whose
    text repeats on at least *min_pages* pages. The table pass has applied
    this rule since the fourth document above; the text pass did not, so the
    same margin stamp was kept out of every table and spliced into the prose
    beside them.
    """
    if doc.page_count < min_pages:
        return {}
    words_by_page = {
        index + 1: _words_where(page, is_vertical) for index, page in enumerate(doc)
    }
    pages_per_text: dict[str, set[int]] = {}
    for page_number, words in words_by_page.items():
        for _, text in words:
            pages_per_text.setdefault(text.lower(), set()).add(page_number)
    stamp_keys = {key for key, pages in pages_per_text.items() if len(pages) >= min_pages}
    return {
        page_number: stamped
        for page_number, words in words_by_page.items()
        if (stamped := [w for w in words if w[1].lower() in stamp_keys])
    }


def drop_skewed_words(words, stamp_boxes) -> list:
    """Remove the stamp's own words from a PyMuPDF ``get_text("words")`` list.

    Matching whole words is what makes this safe. A stamp is printed *across*
    the body text, so its glyphs sit on real words — and because the stamp is
    usually set much larger than the body, one 24 pt glyph can cover most of a
    10 pt word. Any test based on how much of a word the stamp overlaps
    therefore deletes real prose ("The component shall be" -> "The be"); only
    the word's own identity separates the two.
    """
    if not stamp_boxes:
        return words
    return [
        word for word in words
        if not any(_iou(word[:4], stamp) >= MATCH_IOU for stamp in stamp_boxes)
    ]


def is_vertical(a: float, b: float) -> bool:
    """True for a pdfplumber char matrix's genuine 90/270 deg (axis-rotated)
    text — the category ``is_skewed`` deliberately treats as legitimate
    content, but that a margin stamp can also hide in (see module docstring).
    """
    return abs(a) < EPSILON and abs(b) >= EPSILON


def _vertical_words(page) -> list[tuple[tuple, str]]:
    """Group a pdfplumber page's vertical characters into words.

    Same whitespace-grouping idea as ``skewed_words_and_text``, adapted to
    pdfplumber's char dicts. Order along the run (top-to-bottom) is kept as
    the grouping key, not as the word's reading order — a vertical stamp
    authored bottom-to-top comes out reversed here, but every occurrence of
    the same stamp reverses the same way, which is all repetition-matching
    needs.
    """
    chars = [c for c in page.chars if is_vertical(c["matrix"][0], c["matrix"][1])]
    chars.sort(key=lambda c: (round(c["x0"], 1), c["top"]))
    words: list[list[dict]] = []
    current: list[dict] = []
    last_x = None
    for char in chars:
        x = round(char["x0"], 1)
        if (char["text"].isspace() or (current and x != last_x)) and current:
            words.append(current)
            current = []
        if not char["text"].isspace():
            current.append(char)
            last_x = x
    if current:
        words.append(current)
    return [
        (
            (
                min(c["x0"] for c in word), min(c["top"] for c in word),
                max(c["x1"] for c in word), max(c["bottom"] for c in word),
            ),
            "".join(c["text"] for c in word),
        )
        for word in words
    ]


def repeated_vertical_boxes(pdf, *, min_pages: int = STAMP_MIN_PAGES) -> dict[int, list[tuple]]:
    """Return ``{page_number: [bbox, ...]}`` for vertical text that repeats
    across the document — a margin stamp, not a genuine per-page rotated
    label.

    Identity is the word's text (case-insensitive), not position: a stamp
    prints the same word(s) on most pages it appears on, while a one-off
    rotated label (a sideways table header, a chart axis) never repeats.
    Short documents are exempt via *min_pages*, mirroring
    ``ocr.raster.repeated_image_xrefs`` — a handful of pages sharing a word is
    not evidence of a stamp.
    """
    words_by_page: dict[int, list[tuple[tuple, str]]] = {}
    pages_per_text: dict[str, set[int]] = {}
    for index, page in enumerate(pdf.pages):
        page_number = index + 1
        words = _vertical_words(page)
        words_by_page[page_number] = words
        for _, text in words:
            key = text.strip().lower()
            if key:
                pages_per_text.setdefault(key, set()).add(page_number)

    if len(words_by_page) < min_pages:
        return {}
    stamp_keys = {key for key, pages in pages_per_text.items() if len(pages) >= min_pages}
    if not stamp_keys:
        return {}

    boxes_by_page: dict[int, list[tuple]] = {}
    for page_number, words in words_by_page.items():
        boxes = [bbox for bbox, text in words if text.strip().lower() in stamp_keys]
        if boxes:
            boxes_by_page[page_number] = boxes
    return boxes_by_page
