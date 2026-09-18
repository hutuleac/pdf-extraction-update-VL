"""Run Docling with its CodeFormula enrichment on the 20 benchmark pages.

    <venv>/bin/python tests/golden/formulas/run_docling.py <out.json>

Builds a 20-page PDF from the benchmark pages (one Docling run, not twenty
full-course conversions), converts it with the layout model + CodeFormula
(native text layer, no OCR), and writes a ``score.py`` result file: page ->
{"formulas": [...], "seconds": s}. Formulas are every ``FORMULA``-labelled
item's text; ``seconds`` is the whole run divided evenly over the pages.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel

ROOT = Path(__file__).resolve().parents[3]
GT = Path(__file__).parent
PDF = ROOT / "input" / "Geotehnica - note de curs.pdf"


def main(out: Path) -> None:
    pages = sorted(int(p.stem) for p in GT.glob("[0-9]*.json"))
    src = pymupdf.open(PDF)
    subset = pymupdf.open()
    for p in pages:
        subset.insert_pdf(src, from_page=p - 1, to_page=p - 1)
    tmp = Path(tempfile.mkdtemp()) / "formulas-20.pdf"
    subset.save(tmp)

    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=False, do_formula_enrichment=True)
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    t0 = time.time()
    doc = conv.convert(tmp).document
    per_page = round((time.time() - t0) / len(pages), 1)

    results = {str(p): {"formulas": [], "seconds": per_page} for p in pages}
    for item, _ in doc.iterate_items():
        if getattr(item, "label", None) == DocItemLabel.FORMULA and item.prov:
            results[str(pages[item.prov[0].page_no - 1])]["formulas"].append(item.text)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    for p in pages:
        print(f"p{p}: {len(results[str(p)]['formulas'])} formulas")
    print(f"{per_page} s/page")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
