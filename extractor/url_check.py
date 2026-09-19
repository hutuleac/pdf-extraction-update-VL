"""Drops every URL the visual model reads.

The reading model fabricates URLs: on the 388-page course granite invented 11
of the 13 in its kept text, plausible and well-formed and wrong
(``jrbengineering.com`` -> ``thiborgineering.com``). A wrong URL looks like a
citation and Phase 2 would embed it as one. The link is not needed for the
text to be read, so it is removed rather than checked. Native text and OCR keep
theirs: those read the page's own characters, and the model does not.
"""
import re

_URL_RE = re.compile(r'https?://[^\s")\]>,]+|www\.[^\s")\]>,]+')


def drop_model_urls(blocks: list[dict], page_number: int) -> list[dict]:
    """Strip URLs from the page's model-read text and tables, in place.

    Returns one ``VLM_URLS_DROPPED`` warning with the count, or nothing. A text
    block left empty is removed.
    """
    dropped = 0

    def strip(text: str) -> str:
        nonlocal dropped
        text, count = _URL_RE.subn("", text)
        dropped += count
        return re.sub(r"[ \t]{2,}", " ", text).strip() if count else text

    for block in blocks:
        if block.get("source") != "vlm":
            continue
        if block["type"] == "text":
            block["content"] = strip(block["content"])
        elif block["type"] == "table":
            block["content"] = [[strip(cell) for cell in row] for row in block["content"]]
    blocks[:] = [b for b in blocks if not (b["type"] == "text" and not b["content"])]

    if not dropped:
        return []
    return [{"code": "VLM_URLS_DROPPED", "page": page_number, "count": dropped}]
