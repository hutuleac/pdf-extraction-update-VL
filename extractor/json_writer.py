"""JSON output writer (Section 5). Writes the internal model verbatim."""
import json
from pathlib import Path


def write_json(model: dict, out_dir, *, stem: str | None = None) -> Path:
    """Write model to <out_dir>/<stem>.json and return the path.

    `stem` defaults to the filename's stem, but callers processing a batch
    must pass a collision-free stem — two inputs sharing a stem would
    otherwise overwrite each other's output.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or Path(model["document"]["filename"]).stem
    out_path = out_dir / f"{stem}.json"
    out_path.write_text(
        json.dumps(model, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path
