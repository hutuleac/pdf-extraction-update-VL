"""Score a formula recognizer's output against the hand-checked 20-page set.

    python3 tests/golden/formulas/score.py tests/golden/formulas/results/<model>.json

A result file maps page number -> {"formulas": [latex, ...]} — whatever the
recognizer emitted for that page, in any order. Scoring is per expected
formula: *recovered* when its normalized form is a substring of some
candidate's normalized form (a candidate holding two expected formulas counts
for both, since both models merge adjacent lines), else *missing*. Every
candidate that matches no expected formula is *wrong-but-balanced* when it
passes ``formula_is_balanced`` — the dangerous kind, it renders — and
*unbalanced* otherwise (the gate would drop it anyway).

Normalization is deliberately loose about notation and strict about content:
spacing, ``\\left``/``\\right``, ``\\text{...}``, brace style, nu vs v, and the
degree sign are all folded; digits, operators and structure are not.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from extractor.vlm.doctag import formula_is_balanced

HERE = Path(__file__).parent

_DROP = [r"\\left", r"\\right", r"\\tt", r"\\mathrm", r"\\operatorname", r"\\displaystyle",
         r"\\scriptscriptstyle", r"\\scriptstyle", r"\\textstyle", r"\\boldsymbol", r"\\mathbf",
         r"\\big", r"\\Big", r"\\cal", r"\\mathcal", r"\\bullet", r"\\pmb"]
_MAP = {r"\nu": "v", r"\varepsilon": r"\epsilon", r"\cong": r"\approx", r"\to": r"\rightarrow",
        r"\le ": r"\leq ", r"\ge ": r"\geq ", r"\varphi": r"\phi", r"\times": r"\cdot",
        r"\varDelta": r"\Delta", r"\cfrac": r"\frac", r"\dfrac": r"\frac",
        r"\Longrightarrow": r"\Rightarrow", r"\longrightarrow": r"\rightarrow",
        "\u00b0": r"^\circ"}


# Romanian connectors between two formulas on one line ("și", "sau", "cu").
# Matched as words: the character before them, skipping spaces, is not a
# letter and not ``{`` or ``_`` (so ``I_{cu}`` keeps its name), and the character
# after is not a letter (so a spaced-out ``i e s i r e`` keeps its ``s i``).
_GLUE_WORDS = {"si", "sau", "cu", "deci", "și", "şi"}
_GLUE = re.compile(
    r"\\S\s*i|ș\s*i|ş\s*i|(?<=[^A-Za-z\\{_\s])\s*(?:s\s*a\s*u|s\s*i|c\s*u|d\s*e\s*c\s*i)(?=\s|[^A-Za-z]|$)"
)
_SPACING = [r"\\quad", r"\\qquad", r"\\,", r"\\;", r"\\!", r"\\ ", "~"]


def _text(m: re.Match) -> str:
    """``\\text{sat}`` is a subscript, ``\\text{presiunea din}`` is a label."""
    inner = m.group(1).strip()
    if " " in inner or len(inner) > 3 or inner.lower() in _GLUE_WORDS:
        return ""
    return inner


def normalize(latex: str) -> str:
    s = latex.replace("\\\\", " ").replace("&", " ")  # alignment is layout, not content
    s = re.sub(r"\\(?:text|mathrm|operatorname)\s*\{([^{}]*)\}", _text, s)
    s = s.replace(r"\underbrace", "")
    s = re.sub(r"\^\s*\{?\s*\\prime\s*\}?", "'", s)
    s = s.replace(r"\prime", "'")
    for k, v in _MAP.items():
        s = s.replace(k, v)
    for pat in _SPACING:
        s = re.sub(pat, " ", s)
    for pat in _DROP:
        s = re.sub(pat, "", s)
    s = re.sub(r"\s*([{}_^])\s*", r"\1", s)
    s = _GLUE.sub("", s)
    s = re.sub(r"\s+", "", s)
    s = s.replace("{", "").replace("}", "")  # brace style is not content
    s = re.sub(r"\^(0|o|\\circ)(?![0-9A-Za-z])", r"^\\circ", s)  # 27,2^0 == 27,2°
    s = re.sub(r"_+", "_", s)  # granite's _{_{a}}
    s = re.sub(r"\+\++", "+", s)  # "... + \\ & + ..." continues a sum, once
    s = re.sub(r"(?<=\d)\.(?=\d)", ",", s)  # 1.85 for 1,85 is a decimal mark, not a digit
    return s


def score(results: dict) -> dict:
    totals = {"expected": 0, "recovered": 0, "missing": 0, "wrong_balanced": 0, "unbalanced": 0}
    per_page = {}
    for gt_path in sorted(HERE.glob("[0-9]*.json"), key=lambda p: int(p.stem)):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        page = str(gt["page"])
        cands = [c for c in results.get(page, {}).get("formulas", [])]
        norm_c = [normalize(c) for c in cands]
        matched = [False] * len(cands)
        rec, miss = [], []
        for f in gt["formulas"]:
            n = normalize(f["latex"])
            hits = [i for i, c in enumerate(norm_c) if n and n in c]
            if hits:
                rec.append(f["id"])
                for i in hits:
                    matched[i] = True
            else:
                miss.append(f["id"])
        wrong = [cands[i] for i in range(len(cands)) if not matched[i] and formula_is_balanced(cands[i])]
        unbal = [cands[i] for i in range(len(cands)) if not matched[i] and not formula_is_balanced(cands[i])]
        per_page[page] = {"reason": gt["reason"], "expected": len(gt["formulas"]), "recovered": len(rec),
                          "missing": miss, "wrong_balanced": wrong, "unbalanced": unbal}
        totals["expected"] += len(gt["formulas"])
        totals["recovered"] += len(rec)
        totals["missing"] += len(miss)
        totals["wrong_balanced"] += len(wrong)
        totals["unbalanced"] += len(unbal)
    return {"totals": totals, "pages": per_page}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    report = score(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    t = report["totals"]
    print(f"{Path(argv[1]).stem}: recovered {t['recovered']}/{t['expected']} "
          f"({100 * t['recovered'] / t['expected']:.0f}%), wrong-but-balanced {t['wrong_balanced']}, "
          f"unbalanced {t['unbalanced']}")
    for page, p in report["pages"].items():
        flag = "" if not (p["missing"] or p["wrong_balanced"]) else "  <-"
        print(f"  p{page:>3} {p['reason']:<10} {p['recovered']}/{p['expected']}"
              f"  missing={len(p['missing'])} wrong={len(p['wrong_balanced'])} unbalanced={len(p['unbalanced'])}{flag}")
        for w in p["wrong_balanced"]:
            print(f"        WRONG: {w[:110]}")
        for m in p["missing"]:
            print(f"        MISSING: {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
