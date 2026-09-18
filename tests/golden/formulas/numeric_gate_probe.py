"""Measure the numeric formula gate against the hand-checked 20-page set.

    python3 tests/golden/formulas/numeric_gate_probe.py [results/<model>.json]

Runs ``doctag.unsupported_numbers`` — the shipped gate — over every candidate
formula in a result file, against the page's native text layer. Reports, per
page class, how many of the scorer's wrong-but-balanced candidates it flags
(caught) and how many recovered ground-truth formulas it would drop (cost: a
recovered formula whose every holding candidate is flagged). The split by
class is the finding: on `scattered` pages the layer keeps its digits and the
gate is free; on `mismapped` pages the symbol font swallowed them and the gate
rejects correct formulas, which is why the pipeline exempts those pages.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
from score import HERE, normalize, score

sys.path.insert(0, str(HERE.parents[2]))
from extractor.vlm.doctag import unsupported_numbers

PDF = HERE.parents[2] / "input" / "Geotehnica - note de curs.pdf"


def main(result_path: Path) -> None:
    results = json.loads(result_path.read_text(encoding="utf-8"))
    report = score(results)["pages"]
    gt = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in HERE.glob("[0-9]*.json")}
    with pymupdf.open(PDF) as doc:
        native = {page: doc[int(page) - 1].get_text() for page in report}

    def flagged(latex: str, page: str) -> bool:
        return bool(unsupported_numbers(latex, native[page]))

    print(f"{result_path.stem}: wrong-but-balanced {sum(len(r['wrong_balanced']) for r in report.values())}, "
          f"recovered {sum(r['recovered'] for r in report.values())}")
    for reason in ("scattered", "mismapped"):
        caught = wrong = cost = recovered = 0
        for page, r in report.items():
            if r["reason"] != reason:
                continue
            wrong += len(r["wrong_balanced"])
            caught += sum(flagged(w, page) for w in r["wrong_balanced"])
            cands = results.get(page, {}).get("formulas", [])
            for f in gt[page]["formulas"]:
                if f["id"] in r["missing"]:
                    continue
                recovered += 1
                holders = [c for c in cands if normalize(f["latex"]) in normalize(c)]
                if holders and all(flagged(c, page) for c in holders):
                    cost += 1
                    print(f"      would drop {f['id']}: {unsupported_numbers(holders[0], native[page])}")
        print(f"  {reason:<10} caught {caught}/{wrong} wrong, would drop {cost}/{recovered} recovered")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results" / "granite-docling.json")
