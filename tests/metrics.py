"""Semantic quality metrics for extraction output.

Each function takes a model (the dict returned by extract_document) and/or
expected data, and returns a numeric score. Test-time assertions compare these
scores against thresholds defined in one place (test_golden.py).
"""
from __future__ import annotations


def _all_text(model: dict) -> str:
    """Concatenate all text block content from a model."""
    parts: list[str] = []
    for unit in model["pages"]:
        for block in unit["content"]:
            if block["type"] == "text":
                parts.append(block["content"])
    return "\n".join(parts)


def _all_table_cells(model: dict) -> list[list[list[str]]]:
    """Return all table blocks as list of tables, each a list of rows."""
    tables: list[list[list[str]]] = []
    for unit in model["pages"]:
        for block in unit["content"]:
            if block["type"] == "table":
                tables.append(block["content"])
    return tables


# ---------------------------------------------------------------------------
# Phrase retention: what fraction of expected phrases appear in the output?
# ---------------------------------------------------------------------------

def phrase_retention(model: dict, expected_phrases: list[str]) -> float:
    """Fraction of expected_phrases found in the extracted text (0.0-1.0).

    Case-insensitive substring match.
    """
    if not expected_phrases:
        return 1.0
    text = _all_text(model).lower()
    found = sum(1 for phrase in expected_phrases if phrase.lower() in text)
    return found / len(expected_phrases)


# ---------------------------------------------------------------------------
# Duplicate line ratio: repeated lines suggest reading-order or header issues.
# ---------------------------------------------------------------------------

def duplicate_line_ratio(model: dict) -> float:
    """Ratio of duplicate lines to total lines (0.0 = no dupes, 1.0 = all dupes).

    Empty lines are excluded from the count.
    """
    text = _all_text(model)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    unique = set(lines)
    duplicates = len(lines) - len(unique)
    return duplicates / len(lines)


# ---------------------------------------------------------------------------
# Table cell precision/recall against expected cells
# ---------------------------------------------------------------------------

def table_cell_recall(model: dict, expected_cells: list[str]) -> float:
    """Fraction of expected cell values found across all table blocks.

    Case-insensitive exact match on cell text.
    """
    if not expected_cells:
        return 1.0
    all_cells: set[str] = set()
    for table in _all_table_cells(model):
        for row in table:
            for cell in row:
                all_cells.add(cell.lower().strip())
    found = sum(1 for cell in expected_cells if cell.lower().strip() in all_cells)
    return found / len(expected_cells)


def table_cell_precision(model: dict, expected_cells: list[str]) -> float:
    """Fraction of extracted cells that match an expected cell.

    Measures how much noise exists in the tables.
    """
    all_cells: list[str] = []
    for table in _all_table_cells(model):
        for row in table:
            for cell in row:
                if cell.strip():
                    all_cells.append(cell.lower().strip())
    if not all_cells:
        return 1.0
    expected_set = {c.lower().strip() for c in expected_cells}
    matched = sum(1 for cell in all_cells if cell in expected_set)
    return matched / len(all_cells)


# ---------------------------------------------------------------------------
# Reading-order errors: consecutive lines from different columns interleaved
# ---------------------------------------------------------------------------

def reading_order_errors(model: dict, column_markers: list[list[str]]) -> int:
    """Count ordering violations between column marker sequences.

    column_markers is a list of marker lists, one per column. E.g.:
      [["left1", "left2", "left3"], ["right1", "right2", "right3"]]

    An error occurs when a marker from column N appears *after* a marker from
    column N+1 in the extracted text.
    """
    text = _all_text(model).lower()
    if not column_markers or len(column_markers) < 2:
        return 0

    # Find position of each marker in the text
    errors = 0
    for col_idx in range(len(column_markers) - 1):
        left_markers = column_markers[col_idx]
        right_markers = column_markers[col_idx + 1]

        # Get last position of left column and first position of right column
        left_positions = [text.find(m.lower()) for m in left_markers if text.find(m.lower()) >= 0]
        right_positions = [text.find(m.lower()) for m in right_markers if text.find(m.lower()) >= 0]

        if not left_positions or not right_positions:
            continue

        # If any right marker appears before the last left marker, that's an error
        last_left = max(left_positions)
        first_right = min(right_positions)
        if first_right < last_left:
            errors += 1

    return errors


# ---------------------------------------------------------------------------
# Header/footer leakage: detected headers/footers appearing in text blocks
# ---------------------------------------------------------------------------

def header_footer_leakage(model: dict, known_patterns: list[str]) -> int:
    """Count how many known header/footer patterns appear in text blocks.

    Patterns are checked case-insensitively as substrings of text block content.
    Header/footer blocks are NOT checked (they're supposed to contain them).
    """
    count = 0
    for unit in model["pages"]:
        for block in unit["content"]:
            if block["type"] == "text":
                content_lower = block["content"].lower()
                for pattern in known_patterns:
                    if pattern.lower() in content_lower:
                        count += 1
    return count


# ---------------------------------------------------------------------------
# Empty page rate: pages with no content blocks
# ---------------------------------------------------------------------------

def empty_page_rate(model: dict) -> float:
    """Fraction of units that have zero content blocks."""
    units = model["pages"]
    if not units:
        return 0.0
    empty = sum(1 for u in units if not u["content"])
    return empty / len(units)


# ---------------------------------------------------------------------------
# Unicode corruption rate: U+FFFD replacement characters in text
# ---------------------------------------------------------------------------

def unicode_corruption_rate(model: dict) -> float:
    """Ratio of replacement characters to total characters in text blocks."""
    text = _all_text(model)
    if not text:
        return 0.0
    replacements = text.count("\ufffd")
    return replacements / len(text)
