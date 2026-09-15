"""Find the OCR model files without hardcoding one machine's layout.

Search order (first directory that holds both model files wins):
  1. --ocr-model-dir / OcrConfig.model_dir
  2. $KE_OCR_MODEL_DIR
  3. <repo>/models/ocr        (shipped with the repository via git-lfs)
  4. C:/Models/ocr            (Windows only)
  5. ~/.cache/knowledge-extractor/ocr
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Smallest plausible ONNX weight file. Anything below this is a broken
# huggingface_hub symlink stub, not a model.
MIN_MODEL_BYTES = 100_000

DICT_PATH = Path(__file__).with_name("ppocrv6_dict.txt")


def candidate_dirs(explicit: str | None = None) -> list[Path]:
    """Return the model directories to try, in priority order.

    An explicitly named directory is the only one searched: falling back to a
    different model than the one the user asked for would hide their mistake.
    """
    if explicit:
        return [Path(explicit)]

    candidates: list[Path] = []
    env_dir = os.environ.get("KE_OCR_MODEL_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(__file__).resolve().parents[2] / "models" / "ocr")
    if sys.platform == "win32":
        candidates.append(Path("C:/Models/ocr"))
    candidates.append(Path.home() / ".cache" / "knowledge-extractor" / "ocr")
    return candidates


def _find_model(directory: Path, keyword: str) -> Path | None:
    """Return the first usable ``*<keyword>*.onnx`` file directly in *directory*.

    Only the flat directory is searched: a ``models--*/snapshots/`` tree copied
    from another machine can hold symlink stubs that are not real weights.
    """
    try:
        matches = sorted(directory.glob(f"*{keyword}*.onnx"))
    except OSError:
        return None
    for match in matches:
        if match.is_file() and match.stat().st_size >= MIN_MODEL_BYTES:
            return match
    return None


def resolve_models(explicit: str | None = None) -> tuple[Path, Path, Path]:
    """Return (det_model, rec_model, char_dict) or raise OcrUnavailable.

    Raises the most specific reason available: a directory that exists but
    holds only one of the two models reports that missing model, not a missing
    directory.
    """
    from extractor.ocr.base import OcrUnavailable, UnavailableReason

    if not DICT_PATH.is_file():
        raise OcrUnavailable(UnavailableReason.MISSING_DICT, str(DICT_PATH))

    searched: list[Path] = []
    partial: OcrUnavailable | None = None

    for directory in candidate_dirs(explicit):
        searched.append(directory)
        if not directory.is_dir():
            continue
        det = _find_model(directory, "det")
        rec = _find_model(directory, "rec")
        if det and rec:
            return det, rec, DICT_PATH
        if partial is None and (det or rec):
            reason = (
                UnavailableReason.MISSING_REC_MODEL if det
                else UnavailableReason.MISSING_DET_MODEL
            )
            partial = OcrUnavailable(reason, f"in {directory}")

    if partial is not None:
        raise partial
    raise OcrUnavailable(
        UnavailableReason.MISSING_MODEL_DIR,
        "looked in: " + ", ".join(str(p) for p in searched),
    )


def load_labels(dict_path: Path) -> list[str]:
    """Load the character dictionary as PaddleOCR orders it.

    The recognition head emits ``blank + dictionary + space``, so the returned
    list is directly indexable by CTC class id.
    """
    chars = dict_path.read_text(encoding="utf-8").split("\n")
    if chars and chars[-1] == "":
        chars.pop()
    return ["<blank>", *chars, " "]
