"""Render the 136 ground-truth formula crops for a crop-in, LaTeX-out recognizer.

    python3 tests/golden/formulas/render_crops.py <out_dir>

One PNG per expected formula, named by its id, at 200 DPI. Each ground-truth
box is first widened to the native words it overlaps: granite's ``<loc>``
boxes clip the right edge of long equations (73-1 lost its ``kN/m^3``), and a
recognizer must not be scored on pixels it never saw. Then 6 pt of horizontal
and 4 pt of vertical padding.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
GT = Path(__file__).parent
PDF = ROOT / "input" / "Geotehnica - note de curs.pdf"
DPI = 200
PAD = (-6, -4, 6, 4)


def widened(box: pymupdf.Rect, words: list[pymupdf.Rect]) -> pymupdf.Rect:
    out = pymupdf.Rect(box)
    for w in words:
        overlap = w & box
        if not overlap.is_empty and overlap.get_area() > 0.5 * w.get_area() and w.height < 2.5 * box.height:
            out |= w
    return out


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(PDF)
    count = 0
    for path in sorted(GT.glob("[0-9]*.json"), key=lambda p: int(p.stem)):
        gt = json.loads(path.read_text(encoding="utf-8"))
        page = doc[gt["page"] - 1]
        words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
        for formula in gt["formulas"]:
            clip = widened(pymupdf.Rect(formula["bbox"]), words) + PAD
            page.get_pixmap(clip=clip, dpi=DPI, colorspace=pymupdf.csRGB, alpha=False).save(
                out_dir / f"{formula['id']}.png"
            )
            count += 1
    print(f"{count} crops -> {out_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
