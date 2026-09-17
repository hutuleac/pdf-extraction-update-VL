"""Turn PDF pages and image files into RGB arrays an engine can read.

A pixel budget keeps the memory cost bounded: a landscape slide at 300 DPI is a
27 MB frame, so oversized pages get a lower DPI rather than a refusal.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pymupdf

from extractor.errors import ResourceLimitError

MIN_DPI = 72

# An image must be placed on at least this fraction of the pages, and the
# document must have at least this many pages, before the image counts as page
# furniture rather than content. See repeated_image_xrefs.
REPEATED_IMAGE_PAGE_FRACTION = 0.5
REPEATED_IMAGE_MIN_PAGES = 4

# One figure is often stored as several images: authoring tools slice a picture
# into strips, and a strip on its own is unreadable — a 388-page course split
# one diagram into eight bands, none of which meant anything to OCR. Placements
# within this gap (points) that share this fraction of one dimension's span are
# pieces of the same figure and are rendered together. See page_image_regions.
IMAGE_REGION_GAP = 6.0
IMAGE_REGION_SPAN_FRACTION = 0.8

# Icons and bullets aren't content: an image stored smaller than this on either
# edge is dropped before the grouping, so a decorative dot beside a figure
# cannot enlarge the figure's region. Measured on the stored image rather than
# on its placement, because a detailed picture scaled down to a thumbnail still
# holds the pixels OCR needs.
MIN_IMAGE_PIXELS = 50
# ...but a placement this narrow (points) is a glyph sliver or a bullet however
# large the stored image is: a 388-page course placed 3x23 pt strips of a
# single letter at 1206 DPI, 34 of them on one page.
MIN_PLACED_POINTS = 24
# The native density of a headshot placed at 97x63 pt came to 4980 DPI and a
# 25 MP frame. Nothing OCR or a visual model reads needs more than this.
MAX_REGION_DPI = 300
# A merged region covering this much of a page that also carries body text is
# a decorative background (hairline patterns, tinted panels), not a figure.
# A real full-page picture has no text layer and is kept.
BACKGROUND_AREA_FRACTION = 0.85
BACKGROUND_TEXT_CHARS = 200

# Vector figures — schematics, charts, diagrams drawn as paths — have no
# stored image at all: on the reference deck 16 of 17 figure pages had zero
# embedded rasters. `page.cluster_drawings` groups the paths; these thresholds
# separate a figure from a text box, measured on four documents: a callout box
# is 3-5 paths around 6-9 chars per 1000 pt², a schematic is 15-3900 paths
# with under 1 char per 1000 pt². Tables are ruled but text-dense, so the
# density test rejects them too.
# ponytail: a chart with dense axis labels can cross the density line and be
# missed; lower MAX_VECTOR_TEXT_DENSITY if that shows up, and re-measure the
# text-box side before doing so.
MIN_VECTOR_PATHS = 15
MAX_VECTOR_TEXT_DENSITY = 2.0  # chars per 1000 pt²
MIN_VECTOR_POINTS = 60
VECTOR_REGION_DPI = 200


def effective_dpi(page_rect, requested_dpi: int, max_pixels: int) -> int:
    """Return the largest DPI up to *requested_dpi* that fits the pixel budget."""
    width_inches = page_rect.width / 72.0
    height_inches = page_rect.height / 72.0
    area = width_inches * height_inches
    if area <= 0:
        return requested_dpi
    budget_dpi = int((max_pixels / area) ** 0.5)
    return max(MIN_DPI, min(requested_dpi, budget_dpi))


def _pixmap_to_array(pixmap) -> np.ndarray:
    """Convert a PyMuPDF RGB pixmap into an (H, W, 3) uint8 array."""
    buffer = np.frombuffer(pixmap.samples, dtype=np.uint8)
    return buffer.reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]


def page_to_array(page, *, dpi: int, max_pixels: int) -> np.ndarray:
    """Rasterize a whole PyMuPDF page to an RGB array within the pixel budget."""
    dpi = effective_dpi(page.rect, dpi, max_pixels)
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return _pixmap_to_array(pixmap)


def repeated_image_xrefs(
    document,
    *,
    min_pages: int = REPEATED_IMAGE_MIN_PAGES,
    page_fraction: float = REPEATED_IMAGE_PAGE_FRACTION,
) -> set[int]:
    """Return the xrefs of images placed on most pages — logos and stamps.

    A picture that appears on nearly every page is page furniture, not content:
    a corporate logo, a letterhead, a confidentiality stamp. OCR'ing it once per
    page costs a recognition pass per page and returns the same word every time,
    which is noise in the output and the bulk of the OCR time on a
    picture-light document.

    Identity is the xref, so this only matches one stored image reused across
    pages — which is how every authoring tool places a logo. It deliberately
    does not compare pixels: repeated *content* under different xrefs is
    usually bullets and icon fragments, which ``min_side`` already handles.

    Short documents are exempt (*min_pages*): on three pages "appears on most
    pages" is not evidence of anything.
    """
    page_count = document.page_count
    if page_count < min_pages:
        return set()

    pages_per_xref: dict[int, set[int]] = {}
    for number in range(page_count):
        try:
            images = document[number].get_images(full=True)
        except Exception:  # noqa: BLE001 - one unreadable page must not stop the scan
            continue
        for image in images:
            pages_per_xref.setdefault(image[0], set()).add(number)

    threshold = max(min_pages, page_count * page_fraction)
    return {x for x, pages in pages_per_xref.items() if len(pages) >= threshold}


class ImageRegion(NamedTuple):
    """One figure on a page: where it sits, and the density to render it at."""

    rect: pymupdf.Rect
    dpi: int


def _same_figure(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """True when two placements are pieces of one figure.

    Touching is not enough — two unrelated figures in a busy layout overlap at
    a corner. A slice shares nearly all of one dimension's span with its
    neighbours, because that is how the slicing was done.
    """
    if not (a + (-IMAGE_REGION_GAP, -IMAGE_REGION_GAP,  # noqa: RUF005
                 IMAGE_REGION_GAP, IMAGE_REGION_GAP)).intersects(b):
        return False
    x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    y_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return (
        x_overlap >= IMAGE_REGION_SPAN_FRACTION * min(a.width, b.width)
        or y_overlap >= IMAGE_REGION_SPAN_FRACTION * min(a.height, b.height)
    )


def page_image_regions(
    page, *, skip_xrefs: set[int] | None = None, min_pixels: int = MIN_IMAGE_PIXELS,
) -> list[ImageRegion]:
    """Group a page's embedded images into the figures they belong to.

    Each region is rendered *from the page* rather than pulled out by xref, so
    it also carries the vector art and text labels drawn over the bitmaps —
    the callouts and legends of a technical figure usually live there, not in
    the image. ``dpi`` is the highest native density among the images the
    region covers, so nothing is upsampled past what the file holds.

    Images stored smaller than *min_pixels* on either edge are icons and
    bullets; xrefs in *skip_xrefs* are page furniture (see
    ``repeated_image_xrefs``). Vector figures are added from the page's
    drawings (see ``_vector_regions``).
    """
    skip = skip_xrefs or frozenset()
    placed: list[tuple[pymupdf.Rect, float]] = []
    for image in page.get_images(full=True):
        xref, width, height = image[0], image[2], image[3]
        if xref in skip or width < min_pixels or height < min_pixels:
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:  # noqa: BLE001 - one unreadable image must not stop the page
            continue
        for rect in rects:
            rect = pymupdf.Rect(rect)
            if rect.is_empty or rect.is_infinite:
                continue
            if rect.width < MIN_PLACED_POINTS or rect.height < MIN_PLACED_POINTS:
                continue
            density = max(
                width * 72.0 / rect.width if rect.width else 0.0,
                height * 72.0 / rect.height if rect.height else 0.0,
            )
            placed.append((rect, min(density, MAX_REGION_DPI)))

    merged: list[list] = []
    for rect, density in sorted(placed, key=lambda item: (item[0].y0, item[0].x0)):
        merged.append([rect, density])
        joined = True
        while joined:  # a new piece can bridge two groups that were apart
            joined = False
            for i in range(len(merged)):
                for j in range(i + 1, len(merged)):
                    if _same_figure(merged[i][0], merged[j][0]):
                        merged[i][0] |= merged[j][0]
                        merged[i][1] = max(merged[i][1], merged[j][1])
                        del merged[j]
                        joined = True
                        break
                if joined:
                    break

    regions = [ImageRegion(rect, max(MIN_DPI, int(density))) for rect, density in merged]
    page_area = page.rect.get_area()
    if page_area > 0 and any(
        r.rect.get_area() >= BACKGROUND_AREA_FRACTION * page_area for r in regions
    ) and len(page.get_text("text")) >= BACKGROUND_TEXT_CHARS:
        # Eight decorative strips merged into "a figure" the size of the page,
        # over a page of prose — that is the page's background, not a picture.
        regions = [
            r for r in regions if r.rect.get_area() < BACKGROUND_AREA_FRACTION * page_area
        ]
    return regions + _vector_regions(page, [r.rect for r in regions])


def _vector_regions(page, taken: list[pymupdf.Rect]) -> list[ImageRegion]:
    """Figures drawn as vector paths, as regions; see the thresholds above."""
    try:
        drawings = page.get_drawings()
        clusters = page.cluster_drawings(drawings=drawings) if drawings else []
    except Exception:  # noqa: BLE001 - a page whose paths cannot be read has no vector figures
        return []
    page_area = page.rect.get_area()
    regions: list[ImageRegion] = []
    for rect in clusters:
        rect = pymupdf.Rect(rect)
        if rect.width < MIN_VECTOR_POINTS or rect.height < MIN_VECTOR_POINTS:
            continue
        if page_area and rect.get_area() >= BACKGROUND_AREA_FRACTION * page_area:
            continue  # a page-sized frame or background fill
        if any(rect.intersects(t) for t in taken):
            continue  # the paths are the callouts drawn over a raster figure
        paths = sum(1 for d in drawings if rect.intersects(d["rect"]))
        if paths < MIN_VECTOR_PATHS:
            continue
        chars = len(page.get_text("text", clip=rect).strip())
        if chars / (rect.get_area() / 1000.0) > MAX_VECTOR_TEXT_DENSITY:
            continue
        regions.append(ImageRegion(rect, VECTOR_REGION_DPI))
    return regions


def region_pixmap(page, region: ImageRegion, *, max_pixels: int):
    """Render one image region of a page as an RGB pixmap."""
    dpi = effective_dpi(region.rect, region.dpi, max_pixels)
    return page.get_pixmap(
        clip=region.rect, dpi=dpi, colorspace=pymupdf.csRGB, alpha=False,
    )


def page_images_to_arrays(
    page, *, max_pixels: int, skip_xrefs: set[int] | None = None,
) -> list[np.ndarray]:
    """Render a page's figures (see ``page_image_regions``) as RGB arrays.

    Used for 'mixed' and 'layout-complex' pages, where the native text is
    already correct and only the pictures need reading.
    """
    arrays: list[np.ndarray] = []
    for region in page_image_regions(page, skip_xrefs=skip_xrefs):
        try:
            pixmap = region_pixmap(page, region, max_pixels=max_pixels)
        except Exception:  # noqa: BLE001 - one unreadable figure must not stop the page
            continue
        arrays.append(_pixmap_to_array(pixmap).copy())
    return arrays


def _pillow_to_array(source, *, max_pixels: int) -> np.ndarray:
    """Open anything Pillow accepts as an RGB array within the pixel budget."""
    from PIL import Image

    with Image.open(source) as image:
        image = image.convert("RGB")
        pixels = image.width * image.height
        if pixels > max_pixels:
            raise ResourceLimitError("image pixels", pixels, max_pixels)
        return np.asarray(image, dtype=np.uint8)


def image_file_to_array(path: Path | str, *, max_pixels: int) -> np.ndarray:
    """Load an image file as an RGB array, downscaled to the pixel budget."""
    return _pillow_to_array(path, max_pixels=max_pixels)


def image_bytes_to_array(data: bytes, *, max_pixels: int) -> np.ndarray:
    """Load embedded image bytes as an RGB array, downscaled to the budget.

    PowerPoint hands pictures over as blobs rather than files, so the same
    decoding path is reached without writing a temporary file.
    """
    return _pillow_to_array(io.BytesIO(data), max_pixels=max_pixels)
