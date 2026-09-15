"""Image files as documents — a picture is a scanned page with no native text.

Without OCR the file still produces a valid JSON + Markdown pair: zero text
blocks plus a warning naming what could not be read and how to fix it. It never
crashes and never writes a silently empty file.
"""
import logging
from pathlib import Path

from extractor.errors import ResourceLimitError
from extractor.model import make_document, make_unit

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp")


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

    return make_document(
        path.name, "image", 1,
        [make_unit(1, "section", blocks, image_count=1, page_class="scanned")],
        image_count=1,
        warnings=warnings or None,
    )
