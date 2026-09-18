"""Run GOT-OCR2 (page in, Markdown+LaTeX out) on the 20 benchmark pages.

    python3 tests/golden/formulas/run_got_ocr2.py <pages_dir> <out.json> [cpu|mps]

``pages_dir`` holds ``<page>.png`` whole-page renders at 200 DPI. Output is a
``score.py`` result file: page -> {"formulas": [...], "seconds": s, "capped":
bool, "raw": markdown}. ``formulas`` holds every ``\\[...\\]``/``$$...$$``
display span, which is what the ground truth lists and what granite was
scored on; the ``\\(...\\)``/``$...$`` inline spans (units, variable names in
prose) go under ``inline`` where the scorer ignores them. Device defaults to
CPU because the target is a CPU-only Windows laptop.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL = "stepfun-ai/GOT-OCR-2.0-hf"
MAX_NEW_TOKENS = 4096
_DISPLAY = re.compile(r"\\\[(.+?)\\\]|\$\$(.+?)\$\$", re.S)
_INLINE = re.compile(r"\\\((.+?)\\\)|(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.S)


def spans(md: str, pat: re.Pattern) -> list[str]:
    return [next(g for g in m.groups() if g is not None).strip() for m in pat.finditer(md)]


def main(pages_dir: Path, out: Path, device: str) -> None:
    torch.set_num_threads(max(1, torch.get_num_threads()))
    processor = AutoProcessor.from_pretrained(MODEL)
    model = AutoModelForImageTextToText.from_pretrained(MODEL, dtype=torch.float32).to(device).eval()
    results = json.loads(out.read_text()) if out.exists() else {}
    for png in sorted(pages_dir.glob("*.png"), key=lambda p: int(p.stem)):
        if png.stem in results:
            continue
        image = Image.open(png).convert("RGB")
        inputs = processor(image, return_tensors="pt", format=True).to(device)
        t0 = time.time()
        with torch.no_grad():
            ids = model.generate(**inputs, do_sample=False, tokenizer=processor.tokenizer,
                                 stop_strings="<|im_end|>", max_new_tokens=MAX_NEW_TOKENS)
        new = ids[0, inputs["input_ids"].shape[1]:]
        md = processor.decode(new, skip_special_tokens=True)
        results[png.stem] = {"formulas": spans(md, _DISPLAY), "inline": spans(md, _INLINE), "seconds": round(time.time() - t0, 1),
                             "capped": len(new) >= MAX_NEW_TOKENS, "tokens": len(new), "raw": md}
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
        print(f"p{png.stem}: {len(results[png.stem]['formulas'])} formulas, {len(new)} tokens, "
              f"{results[png.stem]['seconds']} s", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "cpu")
