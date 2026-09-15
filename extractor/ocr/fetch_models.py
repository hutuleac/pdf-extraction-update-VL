"""Re-download the PP-OCRv6 weights from Hugging Face.

The weights ship with the repository (models/ocr/, git-lfs), so this script is
only for replacing them — a newer release, or a clone made without git-lfs. It
is never imported by the pipeline and never runs during extraction, which is
what keeps the offline guarantee true.

    pip install -e ".[ocr-hf]"
    python -m extractor.ocr.fetch_models

Needs access to huggingface.co; many corporate networks block it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOS = {
    "ch_PP-OCRv6_small_det_infer.onnx": "PaddlePaddle/PP-OCRv6_small_det_onnx",
    "ch_PP-OCRv6_small_rec_infer.onnx": "PaddlePaddle/PP-OCRv6_small_rec_onnx",
}
REMOTE_FILENAME = "inference.onnx"
DEFAULT_TARGET = Path(__file__).resolve().parents[2] / "models" / "ocr"


def fetch(target_dir: Path) -> list[Path]:
    """Download both model files into *target_dir* and return their paths."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - depends on an optional extra
        raise SystemExit(
            'huggingface-hub is not installed — run: pip install -e ".[ocr-hf]"'
        ) from exc

    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for local_name, repo_id in REPOS.items():
        print(f"Downloading {repo_id} ...")
        cached = hf_hub_download(repo_id=repo_id, filename=REMOTE_FILENAME)
        destination = target_dir / local_name
        destination.write_bytes(Path(cached).read_bytes())
        print(f"  -> {destination} ({destination.stat().st_size / 1e6:.1f} MB)")
        written.append(destination)
    return written


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--target", default=str(DEFAULT_TARGET),
        help=f"Directory to write the .onnx files to (default: {DEFAULT_TARGET})",
    )
    args = parser.parse_args(argv)
    fetch(Path(args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
