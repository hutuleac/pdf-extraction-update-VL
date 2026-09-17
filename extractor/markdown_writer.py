"""Markdown output writer (Section 6)."""
from pathlib import Path

from extractor.warning_text import describe

_UNIT_LABELS = {"page": "Page", "section": "Section", "sheet": "Sheet", "slide": "Slide"}


def _unit_heading(unit: dict) -> str:
    """Return the '## Label N' heading for a unit, with its title when it has one."""
    label = _UNIT_LABELS.get(unit["unit_type"], unit["unit_type"].capitalize())
    heading = f"## {label} {unit['unit']}"
    title = unit.get("title")
    return f"{heading}: {title}" if title else heading


# Source headings nest under the '## Unit N' heading, so level 1 becomes '###'.
_HEADING_OFFSET = 2

_SOURCE_LABELS = {
    "ocr": None,  # rendered with its confidence below
    "vlm": "> Read from the page image by the visual model",
    "vlm-figure": "> Figure description by the visual model",
}


def _cell(value: str) -> str:
    """Make one cell safe for a GFM table row.

    A raw '|' invents a column and a raw newline ends the row, so both are
    neutralised here rather than in the model — JSON keeps the exact cell.
    Backslashes are deliberately left alone: escaping them would rewrite every
    Windows path, regex and LaTeX fragment that appears in a table cell.
    """
    return str(value).replace("|", r"\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _render_table(rows: list[list[str]]) -> str:
    """Render rows as a GitHub-flavored Markdown table (row 0 = header)."""
    if not rows:
        return ""
    # Size the table to the widest row, not to the header. pdfplumber returns
    # ragged tables routinely, and truncating a long row to the header width
    # silently dropped its trailing cells. Padding a short row is safe; losing
    # a cell is not.
    width = max(len(row) for row in rows)
    header, *body = ((row + [""] * width)[:width] for row in rows)
    lines = ["| " + " | ".join(_cell(c) for c in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in body:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return "\n".join(lines)


def _extraction_notes(warnings: list[dict]) -> list[str]:
    """Render the '# Extraction Notes' sidecar body, or nothing when clean.

    Kept out of the document's own .md on purpose: Phase 2 normalizes every
    format into the same internal model so chunking/embedding stays
    format-agnostic, and a warning like "OCR confidence 81%" embedded next to
    real prose would be ingested as if it were document content. The notes
    live in a sidecar file next to it instead — still human-readable, no
    longer inside the thing a future chunker reads.
    """
    if not warnings:
        return []
    lines = ["# Extraction Notes", ""]
    seen: set[str] = set()
    for warning in warnings:
        note = describe(warning)
        if note in seen:
            continue
        seen.add(note)
        lines.append(f"- {note}")
    lines.append("")
    return lines


def write_markdown(model: dict, out_dir, *, stem: str | None = None) -> Path:
    """Write model to <out_dir>/<stem>.md and return the path.

    `stem` defaults to the filename's stem, but callers processing a batch
    must pass a collision-free stem — two inputs sharing a stem would
    otherwise overwrite each other's output.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = model["document"]["filename"]
    stem = stem or Path(filename).stem

    parts: list[str] = [f"# {filename}", ""]
    units = model["pages"]
    for i, unit in enumerate(units):
        parts.append(_unit_heading(unit))
        parts.append("")
        for block in unit["content"]:
            # Header/footer blocks are preserved in JSON but excluded from Markdown.
            if block["type"] in ("header", "footer"):
                continue
            if block["type"] == "heading":
                level = min(6, block.get("level", 1) + _HEADING_OFFSET)
                parts.append(f"{'#' * level} {block['content']}")
                parts.append("")
            elif block["type"] == "text":
                if block["content"]:
                    source = block.get("source")
                    if source == "ocr":
                        confidence = round(block.get("confidence", 0.0) * 100)
                        parts.append(f"> Text recovered by OCR (confidence {confidence}%)")
                        parts.append("")
                    elif _SOURCE_LABELS.get(source):
                        # Labelled so a model's description of a chart is
                        # never mistaken for something the document states.
                        parts.append(_SOURCE_LABELS[source])
                        parts.append("")
                    parts.append(block["content"])
                    parts.append("")
            elif block["type"] == "table":
                parts.append("### Table")
                parts.append("")
                parts.append(_render_table(block["content"]))
                parts.append("")
            elif block["type"] == "image":
                parts.append(f"![]({block['path']})")
                parts.append("")
        if i < len(units) - 1:
            parts.append("---")
            parts.append("")

    out_path = out_dir / f"{stem}.md"
    out_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")

    notes = _extraction_notes(model["document"].get("warnings", []))
    notes_path = out_dir / f"{stem}.notes.md"
    if notes:
        notes_path.write_text("\n".join(notes).rstrip() + "\n", encoding="utf-8")
    elif notes_path.exists():
        # A re-run that cleaned up a previously-warned document must not leave
        # a stale sidecar claiming issues that no longer exist.
        notes_path.unlink()

    return out_path
