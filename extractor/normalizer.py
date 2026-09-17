"""Text normalization rules (FR6). Pure functions, no I/O.

Order of operations in normalize():
  1. ftfy repair (mojibake, encoding errors)
  2. Canonicalize legacy Romanian cedilla diacritics to comma-below
  3. Drop Private Use Area glyphs (undecodable Symbol/Wingdings bullets/operators)
  4. Drop zero-width characters, fold Unicode spaces to ASCII space
  5. Fix hyphenation breaks
  6. Collapse runs of spaces/tabs
  7. Trim each line; collapse runs of blank lines to one (a paragraph break)
"""
import re
from dataclasses import dataclass, field

import ftfy

# Only a word broken across a LINE gets rejoined, so both sides must be letters
# and the hyphen must sit tight against the first half. Without the lookbehind a
# spaced dash ("well - known") and any trailing-hyphen OCR misread ("0-") glue
# themselves to whatever line follows. Requiring the newline matters just as
# much: matching any whitespace would also merge "well- known" — two loosely
# typeset words — into "wellknown", a word that is nowhere in the source.
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-[ \t]*\r?\n[ \t]*(?=[^\W\d_])")
# Romanian has one correct set of diacritics (comma-below, Ș/ș/Ț/ț) but fonts
# and OCR alike also produce the legacy cedilla forms (Ş/ş/Ţ/ţ) — both are
# valid Unicode code points, so ftfy's mojibake repair leaves them alone. A
# document assembled from mixed sources (old vs new fonts, or a scanned page
# next to a native one) ends up with both spellings of the same word. The
# comma-below forms are the Unicode-recommended modern standard, so cedilla
# maps onto them unconditionally.
_CEDILLA_TO_COMMA_BELOW = str.maketrans("ŞşŢţ", "ȘșȚț")

# Old Office documents draw bullets and math operators (+, -, =, bullets,
# arrows) from a Symbol/Wingdings-style font whose glyphs are mapped into the
# Unicode Private Use Area rather than their real code points. PyMuPDF (and
# OCR, reading the same rendered glyph) faithfully returns that PUA code
# point, which is meaningless outside the original font's private mapping —
# there is no reliable way to recover which font meant which glyph (e.g. the
# same PUA slot is '+' in one Symbol-style font and a different mark in
# another), so guessing a replacement risks silently inserting a wrong
# character. Dropping it is the only choice that never fabricates content.
# Covers the BMP PUA plus the two supplementary-plane PUAs.
_PRIVATE_USE_AREA = re.compile(
    "[-\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]"
)

# Zero-width characters carry no meaning and split tokens invisibly, so the
# same word tokenizes differently depending on which PDF it came from.
_ZERO_WIDTH = re.compile(r"[​-‍﻿]")
# Every Unicode space separator becomes a plain space before runs collapse.
# PDFs are full of non-breaking spaces. \n and \r stay out of the class so
# line structure survives for the hyphen rejoin and the line stripping below.
_UNICODE_SPACE = re.compile(r"[^\S\n\r]")
_MULTISPACE = re.compile(r"[ \t]{2,}")
# A blank line is a paragraph break and the one structural signal a chunker
# can rely on; runs of them carry nothing more than one does.
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class NormalizeResult:
    """Result of normalization, including whether ftfy changed the text."""

    text: str
    unicode_repaired: bool = False
    warnings: list[dict] = field(default_factory=list)


def normalize(text: str, *, preserve_layout: bool = False) -> str:
    """Apply normalization rules in order. Returns cleaned text.

    This is the original interface — callers that don't need repair metadata
    can keep using it unchanged.
    """
    return normalize_with_report(text, preserve_layout=preserve_layout).text


def normalize_with_report(text: str, *, preserve_layout: bool = False) -> NormalizeResult:
    """Apply normalization rules and report whether ftfy changed the text.

    Returns a NormalizeResult with the cleaned text and a flag/warning if
    Unicode repair was performed.

    *preserve_layout* keeps indentation and internal spacing (steps 6-7 only
    trim line ends and blank-line runs). Authored Markdown needs it: nested
    lists and fenced code are whitespace, and collapsing it rewrites the
    document.
    """
    if not text:
        return NormalizeResult(text="")

    # 1. ftfy repair — fix mojibake, broken surrogates, etc.
    repaired = ftfy.fix_text(text)
    unicode_repaired = repaired != text
    text = repaired

    # 2. Canonicalize legacy Romanian cedilla to comma-below
    text = text.translate(_CEDILLA_TO_COMMA_BELOW)
    # 3. Drop undecodable Private Use Area glyphs
    text = _PRIVATE_USE_AREA.sub("", text)
    # 4. Invisible whitespace, before anything measures a gap or a run
    text = _ZERO_WIDTH.sub("", text)
    text = _UNICODE_SPACE.sub(" ", text)
    # 5. Fix hyphenation: "procedu-\nra" -> "procedura"
    text = _HYPHEN_BREAK.sub("", text)
    if preserve_layout:
        lines = [line.rstrip() for line in text.splitlines()]
    else:
        # 6. Collapse runs of spaces/tabs to a single space
        text = _MULTISPACE.sub(" ", text)
        lines = [line.strip() for line in text.splitlines()]
    # 7. Blank lines stay as paragraph breaks, one per run. Dropping them all
    # turned a page into one block of hard-wrapped lines with no boundary a
    # chunker could split on.
    text = _BLANK_RUN.sub("\n\n", "\n".join(lines))
    # 8. Trim overall
    text = text.strip()

    warnings: list[dict] = []
    if unicode_repaired:
        warnings.append({"code": "UNICODE_REPAIRED"})

    return NormalizeResult(text=text, unicode_repaired=unicode_repaired, warnings=warnings)
