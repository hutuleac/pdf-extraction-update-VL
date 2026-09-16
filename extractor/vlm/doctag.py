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


def _braces_balanced(latex: str) -> bool:
    r"""True when every ``{`` closes, and none closes early.

    An escaped ``\{`` is a literal brace and is skipped. Depth going negative
    is checked as well as the final total, because ``a } b {`` ends at zero and
    is still broken.
    """
    depth = 0
    for index, char in enumerate(latex):
        if char in "{}" and index and latex[index - 1] == "\\":
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def formula_is_balanced(latex: str) -> bool:
    r"""True when every ``\left`` has a matching ``\right``, and braces close.

    6 of 127 formulas in the spike were unbalanced this way. This is
    deliberately not a full LaTeX parse: it catches the observed failures (a
    truncated or looping formula, and a stray brace) with no renderer, no Node,
    and no new dependency.

    The brace half was added after a stray ``}`` shipped inside an otherwise
    valid ``array`` — ``S _ { K r u m b e i n } = } & \sqrt{...}`` — which
    renders as nothing at all and which the ``\left``/``\right`` count cannot
    see. It costs almost nothing to check: 1 of 479 formulas on the 388-page
    reference course fails it, and that one is the bug.
    """
    return (
        latex.count(r"\left") == latex.count(r"\right")
        and _braces_balanced(latex)
    )


# An environment already opened by the model — wrapping inside it would nest a
# second alignment and break what already renders.
_HAS_ENV = re.compile(r"\\begin\s*\{")


def as_display_math(latex: str) -> str:
    r"""Wrap *latex* for ``$$`` display, adding ``aligned`` when it needs one.

    Both models emit multi-line equations as a bare *alignment body* — the
    inside of an ``align`` environment, with ``&`` marking the alignment column
    and ``\\`` ending each row — and neither emits the environment around it.
    ``$$ V _ 1 & = ... \\ & + ... $$`` is a KaTeX parse error ("Expected 'EOF',
    got '&'"), so the equation does not render at all for the reader.

    This is a rendering fix, not an acceptance one: ``formula_is_balanced``
    passes these already, and they shipped. It is also language-independent —
    an alignment body looks the same in any document, which is why it lives
    here rather than in either parser.
    """
    body = latex.strip()
    if ("&" in body or "\\\\" in body) and not _HAS_ENV.search(body):
        # A trailing row separator would render an empty final row.
        body = re.sub(r"\\\\\s*$", "", body).rstrip()
        body = f"\\begin{{aligned}}\n{body}\n\\end{{aligned}}"
    return f"$$\n{body}\n$$"


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
                pieces.append(as_display_math(content))
            else:
                # Dropped, not emitted broken: a wrong equation that renders is
                # worse than a missing one, because nothing flags it. The caller
                # turns this count into FORMULA_REVIEW_REQUIRED.
                result.rejected_formulas += 1
        elif tag in _PROSE_TAGS:
            pieces.append(content)

    result.text = "\n".join(pieces)
    return result
