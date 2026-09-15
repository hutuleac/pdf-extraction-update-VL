"""granite-docling-258M on mlx — render a page, get its doctag back.

Deliberately thin: everything judgeable lives in doctag.py and apply.py, which
are testable without the weights. Imports of mlx are inside methods so this
module stays importable for tooling on any platform.
"""
from __future__ import annotations

import logging

import pymupdf

from extractor.vlm.config import DEFAULT_MAX_TOKENS

logger = logging.getLogger(__name__)

# The docling conversion prompt the model was trained on. Not a knob: a
# free-form instruction produces prose, not the tag stream doctag.py parses.
PROMPT = "Convert this page to docling."


def render_page(page, *, dpi: int) -> bytes:
    """Rasterize one PyMuPDF page to PNG bytes at *dpi*.

    Whole page, not per-image crops: a formula is assembled from native text,
    small rasters and vector rules, and cropping to embedded images alone
    breaks that assembly apart before the model ever sees it.
    """
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
    return pixmap.tobytes("png")


class GraniteDoclingEngine:
    """Wraps an mlx-vlm model, loaded once and reused for every page."""

    def __init__(self, model_name: str) -> None:
        from mlx_vlm import load

        self.name = model_name
        self._model, self._processor = load(model_name)
        logger.debug("Visual model ready: %s", model_name)

    def convert(self, png_bytes: bytes, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """Return the raw doctag output for one rendered page."""
        import tempfile
        from pathlib import Path

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        # mlx-vlm takes image paths, not buffers.
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            image_path.write_bytes(png_bytes)
            formatted = apply_chat_template(
                self._processor, self._model.config, PROMPT, num_images=1,
            )
            result = generate(
                self._model, self._processor, formatted, [str(image_path)],
                max_tokens=max_tokens, verbose=False,
            )
        return result.text if hasattr(result, "text") else str(result)
