"""JSON file extraction.

A list of flat objects with consistent keys becomes a table block.
Anything else becomes deterministic indented `key: value` text.
One `section` unit. Malformed JSON is reported as a document-level failure.
"""
import json
import logging
from pathlib import Path

from extractor.errors import ResourceLimitError
from extractor.limits import MAX_JSON_CONTAINERS, MAX_JSON_DEPTH
from extractor.model import make_document, make_table_block, make_text_block, make_unit
from extractor.text_loader import load_text_file

logger = logging.getLogger(__name__)

# Rendering recurses per nesting level. Past this depth Python raises
# RecursionError, which the batch loop catches — so the whole file fails and
# its shallow content is lost with it. Truncating with a warning keeps
# everything above the cap and says what was cut.
MAX_NESTING_DEPTH = MAX_JSON_DEPTH


def _is_flat_records(data) -> bool:
    """True if data is a non-empty list of dicts with consistent string keys."""
    if not isinstance(data, list) or not data:
        return False
    if not all(isinstance(item, dict) for item in data):
        return False
    # Check that all dicts have the same keys
    keys = set(data[0].keys())
    return all(set(item.keys()) == keys for item in data)


def _cell(value) -> str:
    """Render one record value for a table cell.

    str() on a nested value emits a Python repr — `{'x': 2}`, single-quoted —
    which is neither valid JSON nor readable prose. Scalars keep their plain
    form so numbers do not gain quotes.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _records_to_table(records: list[dict]) -> list[list[str]]:
    """Convert a list of flat dicts to table rows (header + data)."""
    if not records:
        return []
    headers = list(records[0].keys())
    rows = [headers]
    for record in records:
        rows.append([_cell(record.get(k, "")) for k in headers])
    return rows


def _count_containers(
    data,
    *,
    max_depth: int,
    max_containers: int,
    depth: int = 0,
    count: list[int] | None = None,
) -> None:
    """Reject JSON expansion that exceeds depth or container budgets."""
    if count is None:
        count = [0]
    if isinstance(data, (dict, list)):
        if depth >= max_depth:
            raise ResourceLimitError("JSON depth", depth + 1, max_depth)
        count[0] += 1
        if count[0] > max_containers:
            raise ResourceLimitError("JSON containers", count[0], max_containers)
        values = data.values() if isinstance(data, dict) else data
        for value in values:
            _count_containers(
                value,
                max_depth=max_depth,
                max_containers=max_containers,
                depth=depth + 1,
                count=count,
            )


def _nested_to_text(data, indent: int = 0, truncated: list | None = None) -> str:
    """Render arbitrary JSON as deterministic indented key: value lines.

    Stops at MAX_NESTING_DEPTH and records the fact in *truncated*, so the
    caller can warn instead of the recursion blowing up the whole file.
    """
    lines: list[str] = []
    prefix = "  " * indent

    if indent >= MAX_NESTING_DEPTH and isinstance(data, (dict, list)):
        if truncated is not None:
            truncated.append(indent)
        return f"{prefix}[nesting truncated at depth {MAX_NESTING_DEPTH}]"

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_nested_to_text(value, indent + 1, truncated))
            else:
                lines.append(f"{prefix}{key}: {value}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}[{i}]:")
                lines.append(_nested_to_text(item, indent + 1, truncated))
            else:
                lines.append(f"{prefix}[{i}]: {item}")
    else:
        lines.append(f"{prefix}{data}")

    return "\n".join(lines)


def extract_json(
    path: Path | str,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_containers: int = MAX_JSON_CONTAINERS,
) -> dict:
    """Extract a JSON file into the internal model (one section unit).

    Raises ValueError on malformed JSON so the batch loop can log it as a failure.
    """
    path = Path(path)
    load_result = load_text_file(path)

    try:
        data = json.loads(load_result.text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON in {path.name}: {exc}") from exc

    _count_containers(
        data,
        max_depth=max_depth,
        max_containers=max_containers,
    )

    blocks: list[dict] = []
    warnings = list(load_result.warnings)
    if _is_flat_records(data):
        rows = _records_to_table(data)
        blocks.append(make_table_block(rows))
    else:
        truncated: list[int] = []
        text = _nested_to_text(data, truncated=truncated)
        if truncated:
            warnings.append({"code": "NESTING_TRUNCATED", "depth": MAX_NESTING_DEPTH})
        text_block = make_text_block(text)
        if text_block:
            blocks.append(text_block)

    unit = make_unit(1, "section", blocks)
    return make_document(path.name, "json", 1, [unit], warnings=warnings or None)
