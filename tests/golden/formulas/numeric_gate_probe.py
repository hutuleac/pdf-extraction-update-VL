"""Would a numeric gate catch granite's wrong-but-balanced formulas?

    python3 tests/golden/formulas/numeric_gate_probe.py [results/<model>.json]

Hypothesis: a formula the model *invented* carries numbers the page does not
hold, while a formula it *read* carries numbers the native text layer also
holds (digits survive symbol-font damage; only the operators are mismapped).
For every candidate formula: pull its numeric tokens, look each up in the
page's native text, flag the formula when any is absent. Report, per variant,
how many of the scorer's wrong-but-balanced candidates are flagged (caught)
and how many recovered ground-truth formulas would be flagged too (cost).
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
from score import HERE, normalize, score

PDF = HERE.parents[2] / "input" / "Geotehnica - note de curs.pdf"
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(latex: str, min_digits: int) -> set[str]:
    latex = re.sub(r"\s+", "", latex)  # granite spaces out every character: "0 , 4 5 7"
    return {n.replace(".", ",") for n in _NUM.findall(latex) if len(re.sub(r"\D", "", n)) >= min_digits}


def flagged(latex: str, native_tokens: set[str], native_digits: str, min_digits: int, loose: bool) -> bool:
    nums = numbers(latex, min_digits)
    if not nums:
        return False
    if loose:
        return any(re.sub(r"\D", "", n) not in native_digits for n in nums)
    return any(n not in native_tokens for n in nums)


def main(result_path: Path) -> None:
    results = json.loads(result_path.read_text(encoding="utf-8"))
    report = score(results)["pages"]
    doc = pymupdf.open(PDF)
    pages = {}
    for page in report:
        # a mismapped font hits digits too (page 59 holds "5516" as Odia ୫୫୧୬): fold every
        # Unicode decimal digit to ASCII before looking anything up
        text = "".join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in doc[int(page) - 1].get_text())
        pages[page] = ({n.replace(".", ",") for n in _NUM.findall(text)}, re.sub(r"\D", "", text))
    gt = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in HERE.glob("[0-9]*.json")}

    print(f"{result_path.stem}: wrong-but-balanced {sum(len(r['wrong_balanced']) for r in report.values())}, "
          f"recovered {sum(r['recovered'] for r in report.values())}")
    for min_digits in (1, 2, 3):
        for loose in (False, True):
            caught = cost = has_nums = 0
            missed = []
            for page, r in report.items():
                toks, digs = pages[page]
                for w in r["wrong_balanced"]:
                    if numbers(w, min_digits):
                        has_nums += 1
                    if flagged(w, toks, digs, min_digits, loose):
                        caught += 1
                    else:
                        missed.append(f"p{page}: {w[:70]}")
                # cost: a recovered expected formula is lost if every candidate holding it is flagged
                cands = results.get(page, {}).get("formulas", [])
                for f in gt[page]["formulas"]:
                    if f["id"] in r["missing"]:
                        continue
                    n = normalize(f["latex"])
                    holders = [c for c in cands if n in normalize(c)]
                    if holders and all(flagged(c, toks, digs, min_digits, loose) for c in holders):
                        cost += 1
            mode = "loose" if loose else "token"
            print(f"  min_digits={min_digits} {mode:<5}: caught {caught} (of {has_nums} carrying numbers), "
                  f"would drop {cost} recovered")
            if min_digits == 2 and not loose:
                for m in missed:
                    print(f"      not caught: {m}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results" / "granite-docling.json")
