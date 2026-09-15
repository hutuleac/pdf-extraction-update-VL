"""Image files as documents — a picture is a scanned page with no native text.

Without OCR the file still produces a valid JSON + Markdown pair: zero text
blocks plus a warning naming what could not be read and how to fix it. It never
crashes and never writes a silently empty file.

Under ``--vlm-describe-figures`` the picture is also described, because a
screenshot or an infographic is exactly the case where the glyphs are only half
the content: OCR read a reference card's 148 lines at 0.99 confidence and said
nothing about the diagram they surround.
"""
import logging
from pathlib import Path

from extractor.errors import ResourceLimitError
from extractor.model import make_document, make_unit

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp")


def _describe_figures(image) -> tuple[dict | None, list[dict]]:
    """Describe the picture's figures, or return nothing when that is off.

    Kept out of ``extract_image`` so the visual import stays where it is used:
    the module has to import cleanly on a host with no mlx at all.
    """
    import pymupdf

    from extractor.model import make_vlm_text_block
    from extractor.vlm.describe import describe_image

    height, width = image.shape[:2]
    png = pymupdf.Pixmap(
        pymupdf.csRGB, width, height, image.tobytes(), False,
    ).tobytes("png")
    text, warnings = describe_image(png)
    return make_vlm_text_block(text or "", source="vlm-figure"), warnings


def extract_image(path: Path | str) -> dict:
    """Extract an image file into the internal model as a single scanned unit."""
    from extractor.ocr.apply import ocr_images, unavailable_warning
    from extractor.ocr.config import get_config
    from extractor.ocr.raster import image_file_to_array

    path = Path(path)
    blocks: list[dict] = []
    warnings: list[dict] = []

    try:
        image = image_file_to_array(path, max_pixels=get_config().max_pixels)
    except ResourceLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - an unreadable image is a warning, not a crash
        logger.warning("Could not open image %s: %s", path.name, exc)
        return make_document(
            path.name, "image", 1,
            [make_unit(1, "section", [], image_count=1, page_class="scanned")],
            image_count=1,
            warnings=[{"code": "OCR_FAILED", "page": 1, "detail": str(exc)}],
        )

    block, ocr_warnings = ocr_images([image], page_number=1)
    if block:
        blocks.append(block)
    warnings.extend(ocr_warnings or [unavailable_warning(1)])

    figure_block, describe_warnings = _describe_figures(image)
    if figure_block:
        blocks.append(figure_block)
    warnings.extend(describe_warnings)

    return make_document(
        path.name, "image", 1,
        [make_unit(1, "section", blocks, image_count=1, page_class="scanned")],
        image_count=1,
        warnings=warnings or None,
    )
