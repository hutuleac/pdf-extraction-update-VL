"""Measure what the watermark filter would change on a real PDF.

Reports detection (what is flagged, what is spared) and impact (does the
extracted text actually get cleaner). Read-only: nothing is written, the
pipeline is not touched.

    .venv/Scripts/python.exe tools/watermark_probe.py "input/some.pdf"
    .venv/Scripts/python.exe tools/watermark_probe.py "input/some.pdf" --pages 20,44
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.watermark_filter import (
    AXIS_ROTATED,
    char_filter,
    find_signatures,
    rotation_class,
)

# A word carrying an intrusion looks like "pas6sengers" or "cKonditions" — a
# lowercase run broken by a digit, a capital, or punctuation, then continuing.
# Parentheses are excluded from the class because "exception(s):" is real text,
# and any token holding "@" is skipped because an email address is dotted by
# design. Both were scoring as damage and hiding the true residual.
INTRUSION = re.compile(r"[a-z][0-9A-Z:.,;][a-z]")
WORD = re.compile(r"\S*[a-z]{3}\S*")


def corrupted_word_rate(text: str) -> tuple[int, int]:
    """Count word-like tokens and how many carry an intrusion."""
    words = WORD.findall(text)
    bad = [
        w for w in words
        if "@" not in w and not w[:1].isupper() and INTRUSION.search(w)
    ]
    return len(words), len(bad)


def debris_line_rate(tables: list) -> tuple[int, int]:
    """Count non-empty table cell lines and how many are 1-2 char fragments.

    Empty cells are normal table structure, not damage, so they are excluded
    from both sides of the ratio — counting them buries the signal.
    """
    total = fragments = 0
    for table in tables:
        for row in table:
            for cell in row:
                for line in str(cell or "").split("\n"):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    total += 1
                    if len(stripped) <= 2:
                        fragments += 1
    return total, fragments


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "    n/a"


def report_detection(pdf, detection) -> None:
    print("=" * 72)
    print("DETECTION")
    print("=" * 72)
    print(f"pages {detection.pages}   chars {detection.total_chars}   "
          f"skewed {detection.skewed_chars} "
          f"({pct(detection.skewed_chars, detection.total_chars)})")

    angles = Counter()
    for page in pdf.pages:
        for char in page.chars:
            angles[rotation_class(char["matrix"])] += 1
    print(f"by class: {dict(angles)}")

    print(f"\nflagged as watermark ({len(detection.watermarks)}):")
    for sig in detection.watermarks:
        print(f"  x{detection.signatures[sig]:>4} pages  {sig[:90]!r}")

    rejected = detection.rejected
    print(f"\nskewed but NOT flagged — too rare to be a stamp ({len(rejected)}):")
    for sig, count in sorted(rejected.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  x{count:>4} pages  {sig[:90]!r}")
    if not rejected:
        print("  (none)")

    axis = [
        "".join(c["text"] for c in page.chars
                if rotation_class(c["matrix"]) == AXIS_ROTATED)
        for page in pdf.pages
    ]
    kept = [(i + 1, t) for i, t in enumerate(axis) if t]
    print(f"\naxis-rotated text left untouched ({len(kept)} pages):")
    for page_no, text in kept[:5]:
        print(f"  p{page_no}: {text[:80]!r}")
    if not kept:
        print("  (none)")


def report_impact(path: Path, page_numbers: list[int]) -> None:
    print()
    print("=" * 72)
    print("IMPACT  (before -> after, on the pdfplumber path)")
    print("=" * 72)
    print(f"{'page':>5} {'words':>7} {'corrupt before':>15} {'corrupt after':>14} "
          f"{'debris before':>14} {'debris after':>13}")

    totals = Counter()
    with pdfplumber.open(path) as pdf:
        for page_no in page_numbers:
            page = pdf.pages[page_no - 1]
            clean = page.filter(char_filter)

            words_b, bad_b = corrupted_word_rate(page.extract_text() or "")
            _words_a, bad_a = corrupted_word_rate(clean.extract_text() or "")
            lines_b, frag_b = debris_line_rate(page.extract_tables())
            lines_a, frag_a = debris_line_rate(clean.extract_tables())

            totals.update(words=words_b, bad_b=bad_b, bad_a=bad_a,
                          lines_b=lines_b, frag_b=frag_b,
                          lines_a=lines_a, frag_a=frag_a)
            print(f"{page_no:>5} {words_b:>7} {bad_b:>7} {pct(bad_b, words_b)} "
                  f"{bad_a:>6} {pct(bad_a, words_b)} "
                  f"{frag_b:>6} {pct(frag_b, lines_b)} "
                  f"{frag_a:>5} {pct(frag_a, lines_a)}")

    print("-" * 72)
    print(f"corrupted words : {totals['bad_b']} ({pct(totals['bad_b'], totals['words'])})"
          f"  ->  {totals['bad_a']} ({pct(totals['bad_a'], totals['words'])})")
    print(f"debris lines    : {totals['frag_b']} ({pct(totals['frag_b'], totals['lines_b'])})"
          f"  ->  {totals['frag_a']} ({pct(totals['frag_a'], totals['lines_a'])})")


def report_sample(path: Path, page_no: int) -> None:
    print()
    print("=" * 72)
    print(f"SAMPLE  page {page_no}")
    print("=" * 72)
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_no - 1]
        clean = page.filter(char_filter)
        for label, target in (("BEFORE", page), ("AFTER", clean)):
            text = (target.extract_text() or "")
            print(f"\n--- {label} ---")
            print("\n".join(text.splitlines()[:14]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", help="comma-separated page numbers to measure")
    parser.add_argument("--sample", type=int, default=0,
                        help="print before/after text for this page")
    args = parser.parse_args(argv)

    if not args.pdf.is_file():
        parser.error(f"no such file: {args.pdf}")

    with pdfplumber.open(args.pdf) as pdf:
        report_detection(pdf, find_signatures(pdf))
        page_count = len(pdf.pages)

    if args.pages:
        pages = [int(p) for p in args.pages.split(",")]
        out_of_range = [p for p in pages if not 1 <= p <= page_count]
        if out_of_range:
            parser.error(f"page(s) out of range 1-{page_count}: {out_of_range}")
    else:
        step = max(1, page_count // 12)
        pages = list(range(1, page_count + 1, step))

    report_impact(args.pdf, pages)

    if args.sample:
        report_sample(args.pdf, args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
