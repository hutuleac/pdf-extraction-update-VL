"""Run the official two-stage PaddleOCR-VL pipeline on the 20 benchmark pages.

    python3 tests/golden/formulas/run_paddleocr_vl.py <pages_dir> <out.json> [v1.6|v1.5]

Two stages, as the model is built: PP-DocLayoutV3 finds the regions, then
PaddleOCR-VL-1.6-0.9B (full weights, not the mlx 4-bit quant the pipeline once
ran) reads each region with the prompt for its label. This is the
configuration the whole-page experiment in the backlog never tested. Runs on
Paddle's CPU backend, the same path a Windows laptop takes.

Output is a ``score.py`` result file. ``formulas`` holds the
``display_formula`` blocks (plus any ``$$``/``\\[`` span inside another
block); ``inline_formula`` blocks and inline spans go under ``inline``, which
the scorer ignores, matching how granite and GOT-OCR2 were scored.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from paddleocr import PaddleOCRVL

_DISPLAY = re.compile(r"\\\[(.+?)\\\]|\$\$(.+?)\$\$", re.S)
_INLINE = re.compile(r"\\\((.+?)\\\)|(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.S)
_WRAP = re.compile(r"^\s*(?:\$\$|\\\[|\$)(.*?)(?:\$\$|\\\]|\$)\s*$", re.S)


def spans(md: str, pat: re.Pattern) -> list[str]:
    return [next(g for g in m.groups() if g is not None).strip() for m in pat.finditer(md)]


def unwrap(latex: str) -> str:
    m = _WRAP.match(latex)
    return (m.group(1) if m else latex).strip()


def main(pages_dir: Path, out: Path, version: str) -> None:
    pipeline = PaddleOCRVL(pipeline_version=version, device="cpu")
    results = json.loads(out.read_text()) if out.exists() else {}
    for png in sorted(pages_dir.glob("*.png"), key=lambda p: int(p.stem)):
        if png.stem in results:
            continue
        t0 = time.time()
        (res,) = list(pipeline.predict(str(png)))
        blocks = res.json["res"]["parsing_res_list"]
        formulas, inline = [], []
        for b in blocks:
            label, content = b["block_label"], b["block_content"] or ""
            if label == "display_formula":
                formulas.append(unwrap(content))
            elif label == "inline_formula":
                inline.append(unwrap(content))
            else:
                formulas += spans(content, _DISPLAY)
                inline += spans(content, _INLINE)
        results[png.stem] = {"formulas": formulas, "inline": inline, "seconds": round(time.time() - t0, 1),
                             "labels": [b["block_label"] for b in blocks],
                             "raw": [[b["block_label"], b["block_content"]] for b in blocks]}
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
        print(f"p{png.stem}: {len(formulas)} formulas, {results[png.stem]['seconds']} s", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "v1.6")
