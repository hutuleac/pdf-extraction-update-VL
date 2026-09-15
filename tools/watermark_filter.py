"""Standalone watermark detection for PDFs — not wired into the pipeline.

A stamped watermark ("printed by X on DATE") sits in the native text layer at an
angle. Because both PyMuPDF and pdfplumber order text by position, its glyphs
land *between* body characters and corrupt words from the inside
("pas6sengers", "cKonditions"). The damage is invisible in the page image and
severe in the extracted text.

The signal that separates it from real content is the text matrix. Body text is
axis-aligned; a stamp is not. Two classes matter:

  upright       (a, 0, 0, d)   normal horizontal text
  axis-rotated  (0, b, c, 0)   90/270 deg — legitimate: sideways table headers,
                               spine labels, the author column on a title page
  skewed        everything else — a diagonal stamp

Only ``skewed`` is dropped. pdfplumber's own ``upright`` flag cannot be used
for this: it reports True for a 52 deg stamp and False for real 90 deg content,
which is exactly backwards.

Angle alone can still catch genuinely rotated content — a 45 deg chart label, an
artistic heading. A recurrence rule ("only drop skewed text that repeats across
most pages") was measured against three documents and dropped: it changed no
output, because the upright/axis-rotated/skewed split is what actually spares
real content, and it cost a full document-wide pre-scan (+39% to +114% on the
pdfplumber pass). The caller reports every drop instead, so a genuine rotated
label is surfaced rather than lost. ``find_signatures`` below is kept as a
diagnostic for inspecting a new document, not as part of the filter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A matrix component below this counts as zero. Generous: real axis-aligned
# text is exact, and a stamp is tens of degrees off, so nothing sits near it.
EPSILON = 0.01

# A skewed string must appear on at least this fraction of pages to be called a
# watermark. A stamp is on every page; a rotated chart label is on one.
MIN_PAGE_FRACTION = 0.5

# Signatures shorter than this are ignored — too little evidence that a short
# recurring skewed string is a stamp rather than content.
MIN_SIGNATURE_CHARS = 8

UPRIGHT = "upright"
AXIS_ROTATED = "axis-rotated"
SKEWED = "skewed"


def rotation_class(matrix) -> str:
    """Classify a text matrix as upright, axis-rotated, or skewed."""
    a, b, c, d = matrix[0], matrix[1], matrix[2], matrix[3]
    if abs(b) < EPSILON and abs(c) < EPSILON:
        return UPRIGHT
    if abs(a) < EPSILON and abs(d) < EPSILON:
        return AXIS_ROTATED
    return SKEWED


def is_skewed(char: dict) -> bool:
    """True if a pdfplumber char is drawn at a non-axis-aligned angle."""
    return rotation_class(char["matrix"]) == SKEWED


def _normalize(text: str) -> str:
    """Collapse whitespace so the same stamp matches across pages."""
    return re.sub(r"\s+", " ", text).strip()


def page_skewed_text(page) -> str:
    """Concatenate the skewed characters of one page, in layout order."""
    return _normalize("".join(c["text"] for c in page.chars if is_skewed(c)))


# ---------------------------------------------------------------------------
# Diagnostics — not used by the filter. Answers "what skewed text does this
# document hold, and does it repeat?" when inspecting an unfamiliar PDF.
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    """What the detector found across a document."""

    pages: int = 0
    signatures: dict[str, int] = field(default_factory=dict)
    watermarks: set[str] = field(default_factory=set)
    skewed_chars: int = 0
    total_chars: int = 0

    @property
    def rejected(self) -> dict[str, int]:
        """Skewed strings that did not recur enough to count as watermarks."""
        return {s: n for s, n in self.signatures.items() if s not in self.watermarks}


def find_signatures(
    pdf,
    min_page_fraction: float = MIN_PAGE_FRACTION,
    min_chars: int = MIN_SIGNATURE_CHARS,
) -> Detection:
    """Scan a pdfplumber PDF and decide which skewed strings are watermarks."""
    det = Detection(pages=len(pdf.pages))
    for page in pdf.pages:
        chars = page.chars
        det.total_chars += len(chars)
        skewed = [c for c in chars if is_skewed(c)]
        det.skewed_chars += len(skewed)
        signature = _normalize("".join(c["text"] for c in skewed))
        if len(signature) >= min_chars:
            det.signatures[signature] = det.signatures.get(signature, 0) + 1

    threshold = max(2, int(det.pages * min_page_fraction))
    det.watermarks = {s for s, n in det.signatures.items() if n >= threshold}
    return det


def char_filter(obj) -> bool:
    """pdfplumber ``page.filter`` predicate: keep everything but skewed glyphs.

    Pass directly to ``page.filter(char_filter)``. Non-char objects (lines,
    rects, curves) are always kept — only the text layer is filtered.
    """
    if obj.get("object_type") != "char":
        return True
    return rotation_class(obj["matrix"]) != SKEWED
