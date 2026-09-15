"""Render a page, send it to an mlx-vlm model, get its answer back.

Deliberately thin: everything judgeable lives in the parser modules and
apply.py, which are testable without the weights. One class serves every
supported model — mlx-vlm drives them identically and only the prompt differs,
so the prompt arrives from ``models.py`` rather than being hard-coded here.
Imports of mlx are inside methods so this module stays importable for tooling
on any platform.
"""
from __future__ import annotations

import logging

import pymupdf

from extractor.vlm.config import DEFAULT_MAX_TOKENS, DEFAULT_REPETITION_PENALTY

logger = logging.getLogger(__name__)


def render_page(page, *, dpi: int) -> bytes:
    """Rasterize one PyMuPDF page to PNG bytes at *dpi*.

    Whole page, not per-image crops: a formula is assembled from native text,
    small rasters and vector rules, and cropping to embedded images alone
    breaks that assembly apart before the model ever sees it.
    """
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
    return pixmap.tobytes("png")


class MlxVlmEngine:
    """Wraps an mlx-vlm model, loaded once and reused for every page."""

    def __init__(self, model_name: str, prompt: str) -> None:
        from mlx_vlm import load

        self.name = model_name
        self.prompt = prompt
        self._model, self._processor = load(model_name)
        logger.debug("Visual model ready: %s", model_name)

    def convert(
        self, png_bytes: bytes, *, max_tokens: int = DEFAULT_MAX_TOKENS,
        repetition_penalty: float = DEFAULT_REPETITION_PENALTY,
    ) -> tuple[str, bool]:
        """Return ``(raw output, hit the token cap)`` for one page.

        The second value is the format-agnostic truncation signal: generation
        stopping because it ran out of budget is evidence, where a parser
        inspecting the text can only infer. doctag.py adds its own tag-balance
        check on top; Markdown has no equivalent and relies on this one.
        """
        import tempfile
        from pathlib import Path

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        # mlx-vlm takes image paths, not buffers.
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            image_path.write_bytes(png_bytes)
            formatted = apply_chat_template(
                self._processor, self._model.config, self.prompt, num_images=1,
            )
            result = generate(
                self._model, self._processor, formatted, [str(image_path)],
                max_tokens=max_tokens, verbose=False,
                repetition_penalty=repetition_penalty,
            )

        text = result.text if hasattr(result, "text") else str(result)
        # finish_reason is the direct answer; the token count is the fallback
        # for an mlx-vlm build that does not report one.
        capped = getattr(result, "finish_reason", None) == "length" or (
            getattr(result, "generation_tokens", 0) or 0
        ) >= max_tokens
        return text, capped
