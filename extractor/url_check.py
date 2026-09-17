"""Flags URLs the visual model reads that no other source on the page confirms.

Both VLM models fabricate URLs at a measured, non-trivial rate (see CLAUDE.md's
"Both reading models fabricate URLs" note) — they come back plausible,
well-formed, and wrong. This module does not correct anything: a wrong "fix"
of a plausible-looking URL is worse than flagging it. It only tells the
reader that a URL the model produced did not come from, or does not closely
match, what native text or OCR read on the same page — so it should be
checked against the source before being trusted as a citation.

The same check also catches the opposite case found in practice: OCR
misreading a URL (``login`` -> ``loqin``) while the model reads it correctly.
Either direction is worth a human glance, so this flags disagreement without
guessing which source is right.
"""
import re
from difflib import SequenceMatcher

_URL_RE = re.compile(r'https?://[^\s")\]>,]+|www\.[^\s")\]>,]+')

# Below this, two URLs are different links, not the same link misread once.
_SIMILARITY_THRESHOLD = 0.75

_MODEL_SOURCES = ("vlm", "vlm-figure")


def _urls_in(text: str) -> set[str]:
    return {url.rstrip(".,;:") for url in _URL_RE.findall(text)}


def check_page_urls(blocks: list[dict], page_number: int) -> list[dict]:
    """Return one VLM_URL_UNVERIFIED warning per model-read URL not confirmed
    by native text or OCR on the same page."""
    model_urls: set[str] = set()
    trusted_urls: set[str] = set()
    for block in blocks:
        if block.get("type") != "text":
            continue
        urls = _urls_in(block.get("content", ""))
        if block.get("source") in _MODEL_SOURCES:
            model_urls |= urls
        else:
            trusted_urls |= urls

    warnings = []
    for url in sorted(model_urls - trusted_urls):
        warning = {"code": "VLM_URL_UNVERIFIED", "page": page_number, "model_url": url}
        if trusted_urls:
            closest = max(
                trusted_urls, key=lambda t: SequenceMatcher(None, url, t).ratio(),
            )
            if SequenceMatcher(None, url, closest).ratio() >= _SIMILARITY_THRESHOLD:
                warning["other_url"] = closest
        warnings.append(warning)
    return warnings
