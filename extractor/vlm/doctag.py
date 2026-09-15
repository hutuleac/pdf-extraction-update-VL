"""Turn granite-docling's ``<doctag>`` output into internal-model content.

The model does not emit Markdown. It emits a tag stream — ``<text>``,
``<formula>``, ``<otsl>`` (its table format), ``<picture>``, each prefixed by
four ``<loc_N>`` coordinate tokens. This module is the whole translation, and
it is also where output is judged: see ``is_truncated`` and
``formula_is_balanced``, both calibrated on the failures measured in
docs/spike-2026-09-05-granite-docling.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Four coordinate tokens follow every opening tag; they carry layout we do not
# use, because block order alone is the reading order we need.
_LOC = re.compile(r"<loc_\d+>")

# Tags whose text is prose we keep, in the order the model emitted them.
_PROSE_TAGS = ("text", "section_header_level_1", "list_item", "caption", "other")

# The native pipeline detects headers/footers itself (headers_footers.py) and
# keeps them out of the Markdown. Taking the model's copy as prose would put
# them back, in the middle of the page.
_DROPPED_TAGS = ("page_header", "page_footer")

# Wrappers that hold other blocks rather than text of their own. They are
# unwrapped before the block scan: a non-overlapping scan would otherwise
# match the outermost pair and swallow every block inside it — <doctag> alone
# would consume the entire page.
_CONTAINERS = re.compile(r"</?(?:doctag|unordered_list|ordered_list|group)>")

# Every tag the model opens and closes. Used only for the truncation check.
# ``other`` is excluded on purpose: the model emits it bare as a marker inside
# a picture ("<picture><other></picture>"), so counting it as a paired tag
# reported every illustrated page as truncated.
_PAIRED_TAGS = tuple(
    tag for tag in (
        "doctag", "unordered_list", "ordered_list", "group", "picture", "otsl",
        "formula", *_PROSE_TAGS, *_DROPPED_TAGS,
    ) if tag != "other"
)

_BLOCK = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL)
_OTSL = re.compile(r"<otsl>(.*?)</otsl>", re.DOTALL)
# OTSL cells are not closed: "<fcel>Uscat<fcel>0 - 0,40". Content runs from one
# cell tag to the next tag of any kind.
_CELL = re.compile(r"<(ched|fcel|ecel|rhed|lcel|ucel|xcel|nl)>([^<]*)")

_SPAN_CELLS = ("lcel", "ucel", "xcel")


@dataclass
class ParsedPage:
    """One page of model output, translated and judged."""

    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    # The accepted formulas on their own, so a page whose prose is already in
    # the text layer can still contribute the equations that are not.
    formulas: list[str] = field(default_factory=list)
    formula_count: int = 0
    rejected_formulas: int = 0
    has_picture: bool = False


def strip_locations(raw: str) -> str:
    """Drop the ``<loc_N>`` coordinate tokens that prefix every tag's content."""
    return _LOC.sub("", raw)


def is_truncated(raw: str) -> bool:
    """True when generation stopped before closing its tags.

    Measured on 10 of 35 garbled pages in the spike. It is the cheapest
    reliable signal that the model fell into a repetition loop and burned the
    token budget — page 176 repeated one fraction until the cap. A tag that
    never closes counts too: ``<doctag><formula>x = 1</doctag>`` ends tidily
    and is still a formula cut in half.
    """
    text = raw.strip()
    if not text.endswith("</doctag>"):
        return True
    body = strip_locations(text)
    return any(
        body.count(f"<{tag}>") != body.count(f"</{tag}>") for tag in _PAIRED_TAGS
    )


def formula_is_balanced(latex: str) -> bool:
    r"""True when every ``\left`` has a matching ``\right``.

    6 of 127 formulas in the spike were unbalanced. This is deliberately not a
    full LaTeX parse: it catches the observed failure (a truncated or looping
    formula) with no renderer, no Node, and no new dependency.
    """
    return latex.count(r"\left") == latex.count(r"\right")


def _parse_otsl(body: str) -> list[list[str]]:
    """Turn one OTSL table body into rows of cell strings."""
    rows: list[list[str]] = []
    row: list[str] = []
    for tag, content in _CELL.findall(body):
        if tag == "nl":
            rows.append(row)
            row = []
        elif tag in _SPAN_CELLS:
            # A span continuation carries no text of its own; keep the column
            # so rows stay rectangular for the Markdown writer.
            row.append("")
        else:
            row.append(content.strip())
    if row:
        rows.append(row)
    return [r for r in rows if r]


def parse(raw: str) -> ParsedPage:
    """Translate one page of doctag output into prose, tables and counts."""
    result = ParsedPage()
    body = strip_locations(raw)

    for match in _OTSL.finditer(body):
        rows = _parse_otsl(match.group(1))
        if rows:
            result.tables.append(rows)
    # Remove tables before reading prose, so cell text is not emitted twice.
    body = _OTSL.sub("", body)

    result.has_picture = "<picture>" in body
    body = _CONTAINERS.sub("", body)

    pieces: list[str] = []
    for tag, content in _BLOCK.findall(body):
        if tag in _DROPPED_TAGS or tag == "picture":
            continue
        content = content.strip()
        if not content:
            continue
        if tag == "formula":
            if formula_is_balanced(content):
                result.formula_count += 1
                result.formulas.append(content)
                pieces.append(f"$$\n{content}\n$$")
            else:
                # Dropped, not emitted broken: a wrong equation that renders is
                # worse than a missing one, because nothing flags it. The caller
                # turns this count into FORMULA_REVIEW_REQUIRED.
                result.rejected_formulas += 1
        elif tag in _PROSE_TAGS:
            pieces.append(content)

    result.text = "\n".join(pieces)
    return result
