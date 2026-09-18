"""Backlog step 2: can PyMuPDF text blocks alone find the formula regions?

    python3 tests/golden/formulas/region_probe.py

Throwaway measurement, kept so the heuristic can be tuned against the same
numbers. Three block signals: median line length under 4 chars, a maths font,
or a script ``_unexpected_scripts`` flags. Blocks within 6 pt are merged.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from extractor.page_signals import _unexpected_scripts  # noqa: E402

GT = Path(__file__).parent
PDF = ROOT / "input" / "Geotehnica - note de curs.pdf"
MATH_FONTS = ("symbol", "math", "euclid", "mtextra", "mt extra", "times new roman,italic")
SHORT_LINE = 4
MERGE_GAP = 6


def candidate_blocks(page) -> list[pymupdf.Rect]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        lines = [line for line in block["lines"]
                 if "".join(s["text"] for s in line["spans"]).strip()]
        if not lines:
            continue
        texts = ["".join(s["text"] for s in line["spans"]).strip() for line in lines]
        fonts = {s["font"].lower() for line in lines for s in line["spans"]}
        short = statistics.median(len(t) for t in texts) < SHORT_LINE
        math_font = any(k in f for f in fonts for k in MATH_FONTS)
        script = bool(_unexpected_scripts("".join(texts)))
        if short or math_font or script:
            out.append(pymupdf.Rect(block["bbox"]))
    return out


def merge(rects: list[pymupdf.Rect], gap: float = MERGE_GAP) -> list[pymupdf.Rect]:
    rects = list(rects)
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(rects):
            grown = pymupdf.Rect(a.x0 - gap, a.y0 - gap, a.x1 + gap, a.y1 + gap)
            for j in range(i + 1, len(rects)):
                if grown.intersects(rects[j]):
                    rects[i] = a | rects[j]
                    del rects[j]
                    merged = True
                    break
            if merged:
                break
    return rects


def iou(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    i = a & b
    if i.is_empty:
        return 0.0
    return i.get_area() / (a.get_area() + b.get_area() - i.get_area())


def covered(expected: pymupdf.Rect, region: pymupdf.Rect) -> float:
    """Fraction of *expected*'s area that lies inside *region*."""
    i = expected & region
    return 0.0 if i.is_empty else i.get_area() / expected.get_area()


def main() -> None:
    doc = pymupdf.open(PDF)
    tot = {"exp": 0, "iou": 0, "cov": 0, "regions": 0, "empty": 0}
    for path in sorted(GT.glob("[0-9]*.json"), key=lambda p: int(p.stem)):
        gt = json.loads(path.read_text(encoding="utf-8"))
        regions = merge(candidate_blocks(doc[gt["page"] - 1]))
        exp = [pymupdf.Rect(x["bbox"]) for x in gt["formulas"]]
        hit_iou = sum(1 for e in exp if any(iou(e, r) > 0.5 for r in regions))
        hit_cov = sum(1 for e in exp if any(covered(e, r) >= 0.8 for r in regions))
        empty = sum(1 for r in regions if not any(covered(e, r) > 0.3 for e in exp))
        tot["exp"] += len(exp)
        tot["iou"] += hit_iou
        tot["cov"] += hit_cov
        tot["regions"] += len(regions)
        tot["empty"] += empty
        print(f"p{gt['page']:>3} {gt['reason']:<10} expected={len(exp):>2} regions={len(regions):>2} "
              f"iou>0.5={hit_iou:>2} covered80={hit_cov:>2} empty_regions={empty}")
    print(f"\nTOTAL expected={tot['exp']} iou>0.5={tot['iou']} ({100 * tot['iou'] / tot['exp']:.0f}%) "
          f"covered80={tot['cov']} ({100 * tot['cov'] / tot['exp']:.0f}%) "
          f"regions={tot['regions']} empty={tot['empty']}")


if __name__ == "__main__":
    main()
