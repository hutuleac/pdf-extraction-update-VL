"""Knowledge Extraction Pipeline — Faza 1 orchestrator.

Reads input files (.pdf, .csv, .docx, .xlsx), writes output/json/*.json and
output/markdown/*.md. Runs 100% locally.

Usage:
    python main.py                          # defaults: input/ -> output/
    python main.py --input docs --output out --max-file-mb 5
"""
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from extractor.dispatcher import SUPPORTED_EXTENSIONS, extract_document
from extractor.errors import classify_exception
from extractor.json_writer import write_json
from extractor.limits import MAX_FILE_MB, FileTooLargeError, check_file_size
from extractor.markdown_writer import write_markdown
from extractor.ocr.config import DEFAULT_DPI, DEFAULT_MIN_CONFIDENCE, configure
from extractor.vlm.config import DEFAULT_DPI as VLM_DEFAULT_DPI
from extractor.vlm.config import DEFAULT_MAX_TOKENS, DEFAULT_MODEL
from extractor.vlm.config import configure as configure_vlm
from extractor.vlm.models import SPECS as VLM_SPECS
from extractor.warning_text import describe

logger = logging.getLogger(__name__)

# Codes that mean content exists but was not extracted — worth an actionable hint.
_UNREADABLE_CODES = ("OCR_UNAVAILABLE", "OCR_SKIPPED_DISABLED", "OCR_MODEL_INCOMPATIBLE",
                     "OCR_FAILED", "OCR_REJECTED_LOW_CONFIDENCE")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all supported options."""
    parser = argparse.ArgumentParser(
        prog="extract",
        description="Knowledge Extraction Pipeline — convert documents to JSON and Markdown.",
    )
    parser.add_argument(
        "--input", dest="input_dir", default="input",
        help="Directory containing input files (default: input)",
    )
    parser.add_argument(
        "--output", dest="output_dir", default="output",
        help="Root output directory for json/ and markdown/ subdirs (default: output)",
    )
    parser.add_argument(
        "--log-file", dest="log_file", default="logs/extraction.log",
        help="Path to the log file (default: logs/extraction.log)",
    )
    parser.add_argument(
        "--max-file-mb", dest="max_file_mb", type=int, default=MAX_FILE_MB,
        help=f"Skip files larger than this (MB). Default: {MAX_FILE_MB}",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Set log level to DEBUG",
    )

    ocr_group = parser.add_argument_group("OCR")
    ocr_group.add_argument(
        "--no-ocr", dest="ocr", action="store_false",
        help="Do not read scanned pages or image files, even if OCR is available",
    )
    ocr_group.add_argument(
        "--ocr-figures", dest="ocr_figures", action="store_true",
        help=(
            "Also read the pictures on pages whose native text is fine. Off by "
            "default because on chart-heavy documents it returns mostly axis "
            "labels; turn it on for infographic decks, where the figure holds "
            "the page's only text"
        ),
    )
    ocr_group.add_argument(
        "--ocr-model-dir", dest="ocr_model_dir", default=None,
        help="Directory holding the detection and recognition .onnx files",
    )
    ocr_group.add_argument(
        "--ocr-dpi", dest="ocr_dpi", type=int, default=DEFAULT_DPI,
        help=f"Resolution used to rasterize scanned pages. Default: {DEFAULT_DPI}",
    )
    ocr_group.add_argument(
        "--ocr-min-confidence", dest="ocr_min_confidence", type=float,
        default=DEFAULT_MIN_CONFIDENCE,
        help=(
            "Below this confidence, OCR text is flagged as uncertain and never "
            f"replaces damaged native text. Default: {DEFAULT_MIN_CONFIDENCE}"
        ),
    )
    vlm_group = parser.add_argument_group("Visual model")
    vlm_group.add_argument(
        "--vlm", dest="vlm", action="store_true",
        help=(
            "Read every page with granite-docling as well, recovering formulas "
            "and structure the text layer does not carry. Apple Silicon only, "
            "and slow — roughly 14 s per page"
        ),
    )
    vlm_group.add_argument(
        "--vlm-model", dest="vlm_model", default=DEFAULT_MODEL,
        help=(
            "Model to load. The name selects the output format too, so it must "
            f"contain one of: {', '.join(sorted(VLM_SPECS))}. "
            f"Default: {DEFAULT_MODEL}"
        ),
    )
    vlm_group.add_argument(
        "--vlm-dpi", dest="vlm_dpi", type=int, default=VLM_DEFAULT_DPI,
        help=f"Resolution used to render pages for the model. Default: {VLM_DEFAULT_DPI}",
    )
    vlm_group.add_argument(
        "--vlm-max-tokens", dest="vlm_max_tokens", type=int, default=DEFAULT_MAX_TOKENS,
        help=f"Token budget per page. Default: {DEFAULT_MAX_TOKENS}",
    )
    vlm_group.add_argument(
        "--vlm-cache-dir", dest="vlm_cache_dir", default=None,
        help=(
            "Where per-page inferences are cached, so an interrupted run does "
            "not re-infer. Default: ~/.cache/knowledge-extractor/vlm"
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: str, *, verbose: bool = False) -> None:
    """Configure dual logging: console + file, format 'LEVEL - message'."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Close before dropping: clear() alone leaks the open file. Harmless in a
    # single CLI run, a real leak across repeated calls in tests and in the
    # long-lived launcher process.
    for handler in root.handlers:
        handler.close()
    root.handlers.clear()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # Windows consoles default to a legacy code page, which turns dashes and
    # diacritics in warning text into replacement characters.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def unique_stems(paths: list[Path]) -> dict[Path, str]:
    """Map each input path to a collision-free output stem.

    Two inputs sharing a stem ('spec.pdf', 'spec.docx') would otherwise write
    the same output file and one would silently overwrite the other.
    """
    counts = Counter(p.stem for p in paths)
    return {
        p: p.stem if counts[p.stem] == 1 else f"{p.stem}-{p.suffix.lstrip('.')}"
        for p in paths
    }


def process_file(
    path: Path, json_dir: Path, md_dir: Path, *, stem: str | None = None,
) -> tuple[Path, Path, dict]:
    """Process one input file. Returns (json_path, md_path, model).

    `stem` defaults to the input's own stem; callers processing a batch must
    pass a collision-free one (see `unique_stems`).
    """
    stem = stem or path.stem
    logger.info("Processing %s", path.name)
    images_dir = md_dir / f"{stem}_images"
    model = extract_document(path, images_dir=images_dir)
    logger.info("%d units extracted", model["document"]["pages"])
    json_path = write_json(model, json_dir, stem=stem)
    logger.info("JSON generated")
    md_path = write_markdown(model, md_dir, stem=stem)
    logger.info("Markdown generated")
    return json_path, md_path, model


def file_stats(model: dict) -> dict:
    """Summarize one processed document: block counts, units, images."""
    doc = model["document"]
    units = model["pages"]
    text_blocks = sum(
        1 for unit in units for b in unit["content"] if b["type"] == "text"
    )
    table_blocks = sum(
        1 for unit in units for b in unit["content"] if b["type"] == "table"
    )
    warnings = doc.get("warnings", [])
    return {
        "filename": doc["filename"],
        "source_type": doc["source_type"],
        "units": doc["pages"],
        "text_blocks": text_blocks,
        "table_blocks": table_blocks,
        "image_count": doc["image_count"],
        "ocr_pages": sum(1 for w in warnings if w["code"] == "OCR_APPLIED"),
        "vlm_pages": sum(1 for w in warnings if w["code"] == "VLM_APPLIED"),
        "vlm_formulas": sum(w.get("formulas", 0) for w in warnings if w["code"] == "VLM_APPLIED"),
        "unreadable_pages": sum(1 for w in warnings if w["code"] in _UNREADABLE_CODES),
        "warnings": warnings,
        "ok": True,
    }


def format_summary(results: list[dict]) -> list[str]:
    """Build the end-of-run summary lines from per-file stats."""
    succeeded = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    total_units = sum(r["units"] for r in succeeded)
    total_text = sum(r["text_blocks"] for r in succeeded)
    total_tables = sum(r["table_blocks"] for r in succeeded)
    total_images = sum(r["image_count"] for r in succeeded)

    lines = ["", "=" * 60, "Run complete."]
    lines.append(
        f"{len(results)} file(s): {len(succeeded)} succeeded, {len(failed)} failed"
    )
    for r in succeeded:
        lines.append(
            f"  [OK]   {r['filename']} ({r['source_type']}): "
            f"{r['units']} unit(s), {r['text_blocks']} text, "
            f"{r['table_blocks']} table, {r['image_count']} image(s)"
        )
    for r in failed:
        lines.append(f"  [FAILED] {r['filename']}: {r.get('error', 'unknown error')}")
    lines.append(
        f"Totals: {total_units} units, {total_text} text block(s), "
        f"{total_tables} table block(s), {total_images} image(s)"
    )
    lines.extend(_warning_rollup(succeeded))
    lines.append("=" * 60)
    return lines


def _warning_rollup(succeeded: list[dict]) -> list[str]:
    """Report what could not be extracted, and name the fix exactly once."""
    ocr_pages = sum(r.get("ocr_pages", 0) for r in succeeded)
    vlm_pages = sum(r.get("vlm_pages", 0) for r in succeeded)
    unreadable = sum(r.get("unreadable_pages", 0) for r in succeeded)
    if not ocr_pages and not vlm_pages and not unreadable:
        return []

    lines: list[str] = []
    if ocr_pages:
        lines.append(f"OCR recovered text on {ocr_pages} page(s).")
    if vlm_pages:
        formulas = sum(r.get("vlm_formulas", 0) for r in succeeded)
        lines.append(
            f"The visual model read {vlm_pages} page(s), recovering {formulas} formula(s)."
        )
    if not unreadable:
        return lines

    lines.append(f"{unreadable} page(s) could not be extracted:")
    seen: set[str] = set()
    for result in succeeded:
        for warning in result.get("warnings", []):
            if warning["code"] not in _UNREADABLE_CODES:
                continue
            note = f"  - {result['filename']}: {describe(warning)}"
            if note not in seen:
                seen.add(note)
                lines.append(note)
    lines.append(
        '  Fix: install OCR with  pip install -e ".[ocr]"  and put the PP-OCRv6 '
        "detection and recognition .onnx files in models/ocr/ "
        "(or point --ocr-model-dir at them)."
    )
    return lines


def discover_inputs(input_path: Path) -> list[Path]:
    """Return sorted input files whose extension is supported."""
    files = [
        p for p in input_path.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Discover and process all supported files in input_dir.

    Returns a process exit code, so a scheduler, CI step or the desktop
    launcher can tell a failed batch from a clean one:
    0 = every file processed, 1 = at least one file failed, 2 = the run could
    not start because the input directory does not exist.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(args.log_file, verbose=args.verbose)

    configure(
        enabled=args.ocr,
        figures=args.ocr_figures,
        model_dir=args.ocr_model_dir,
        dpi=args.ocr_dpi,
        min_confidence=args.ocr_min_confidence,
    )
    configure_vlm(
        enabled=args.vlm,
        model=args.vlm_model,
        dpi=args.vlm_dpi,
        max_tokens=args.vlm_max_tokens,
        cache_dir=args.vlm_cache_dir,
    )

    input_path = Path(args.input_dir)
    json_dir = Path(args.output_dir) / "json"
    md_dir = Path(args.output_dir) / "markdown"
    max_bytes = args.max_file_mb * 1024 * 1024

    if not input_path.is_dir():
        logger.error("Input directory does not exist: %s", input_path)
        return 2

    files = discover_inputs(input_path)
    if not files:
        logger.info("No supported files found in %s", input_path)
        return 0

    stems = unique_stems(files)

    results: list[dict] = []
    for path in files:
        # --- size guard ---
        try:
            check_file_size(path, max_bytes=max_bytes)
        except FileTooLargeError as exc:
            failure = classify_exception(exc)
            logger.warning("Skipped %s [%s]: %s", path.name, failure.category.value, failure)
            results.append({
                "filename": path.name,
                "ok": False,
                "error_category": failure.category.value,
                "error": str(failure),
            })
            continue

        # --- extraction ---
        try:
            _json_path, _md_path, model = process_file(path, json_dir, md_dir, stem=stems[path])
            results.append(file_stats(model))
        except Exception as exc:  # noqa: BLE001 - reliability: skip bad document
            failure = classify_exception(exc)
            logger.error(
                "Failed to process %s [%s]: %s",
                path.name,
                failure.category.value,
                failure,
            )
            results.append({
                "filename": path.name,
                "ok": False,
                "error_category": failure.category.value,
                "error": str(failure),
            })

    for line in format_summary(results):
        logger.info(line)

    return 1 if any(not r.get("ok") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
