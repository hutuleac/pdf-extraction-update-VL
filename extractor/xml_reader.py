"""XML file extraction using defusedxml.

Renders the tree as indented path/text lines with external entities disabled.
One `section` unit. Malicious XML (XXE) is refused, not resolved.
"""
import logging
from pathlib import Path

import defusedxml.ElementTree as ET

from extractor.errors import ResourceLimitError
from extractor.limits import MAX_XML_DEPTH, MAX_XML_NODES
from extractor.model import make_document, make_text_block, make_unit
from extractor.text_loader import load_text_file

logger = logging.getLogger(__name__)

# Rendering recurses per element level. Past this depth Python raises
# RecursionError, which the batch loop catches — so the whole file fails and
# its shallow content is lost with it. Truncating with a warning keeps
# everything above the cap and says what was cut.
MAX_NESTING_DEPTH = MAX_XML_DEPTH


def _tag_local_name(tag: str) -> str:
    """Strip namespace URI from a tag, e.g. '{http://...}root' -> 'root'."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _render_tree(
    element,
    indent: int = 0,
    truncated: list | None = None,
    *,
    max_depth: int = MAX_XML_DEPTH,
    node_count: list[int] | None = None,
    max_nodes: int = MAX_XML_NODES,
) -> list[str]:
    """Recursively render an XML element as indented path/text lines.

    Stops at MAX_NESTING_DEPTH and records the fact in *truncated*, so the
    caller can warn instead of the recursion blowing up the whole file.
    """
    lines: list[str] = []
    prefix = "  " * indent
    if node_count is None:
        node_count = [0]
    node_count[0] += 1
    if node_count[0] > max_nodes:
        raise ResourceLimitError("XML nodes", node_count[0], max_nodes)
    tag = _tag_local_name(element.tag)

    if indent >= max_depth:
        raise ResourceLimitError("XML depth", indent + 1, max_depth)

    # Render attributes inline
    attrs = ""
    if element.attrib:
        attr_parts = [f'{k}="{v}"' for k, v in element.attrib.items()]
        attrs = " " + " ".join(attr_parts)

    text = (element.text or "").strip()
    tail = (element.tail or "").strip()

    children = list(element)
    if not children and text:
        # Leaf with text content
        lines.append(f"{prefix}{tag}{attrs}: {text}")
    elif children:
        lines.append(f"{prefix}{tag}{attrs}:")
        if text:
            lines.append(f"{prefix}  {text}")
        for child in children:
            lines.extend(
                _render_tree(
                    child,
                    indent + 1,
                    truncated,
                    max_depth=max_depth,
                    node_count=node_count,
                    max_nodes=max_nodes,
                )
            )
    else:
        # Empty element
        lines.append(f"{prefix}{tag}{attrs}")

    if tail:
        lines.append(f"{prefix}{tail}")

    return lines


def extract_xml(
    path: Path | str,
    *,
    max_depth: int = MAX_XML_DEPTH,
    max_nodes: int = MAX_XML_NODES,
) -> dict:
    """Extract an XML file into the internal model (one section unit).

    Uses defusedxml to prevent XXE attacks. Raises ValueError on parse failure.
    """
    path = Path(path)
    load_result = load_text_file(path)

    try:
        root = ET.fromstring(load_result.text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in {path.name}: {exc}") from exc
    except Exception as exc:  # defusedxml raises various on XXE
        raise ValueError(f"Refused XML in {path.name}: {exc}") from exc

    truncated: list[int] = []
    lines = _render_tree(
        root,
        truncated=truncated,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    text = "\n".join(lines)

    warnings = list(load_result.warnings)
    if truncated:
        warnings.append({"code": "NESTING_TRUNCATED", "depth": MAX_NESTING_DEPTH})

    blocks: list[dict] = []
    text_block = make_text_block(text)
    if text_block:
        blocks.append(text_block)

    unit = make_unit(1, "section", blocks)
    return make_document(path.name, "xml", 1, [unit], warnings=warnings or None)
