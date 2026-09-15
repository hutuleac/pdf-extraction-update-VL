"""Tests for extractor.ocr.raster — needs no model weights, only PyMuPDF."""
import pymupdf

from extractor.ocr.raster import (
    page_image_regions,
    page_images_to_arrays,
    repeated_image_xrefs,
)


def _pdf_with_embedded_pixmap(pixmap: pymupdf.Pixmap) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 200, 200), pixmap=pixmap)
    return doc


def _pixmap(color: int) -> pymupdf.Pixmap:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200))
    pixmap.clear_with(color)
    return pixmap


def _pdf_with_logo(pages: int, logo_on: int, unique_on: int = 0) -> pymupdf.Document:
    """A document whose *logo_on* first pages share one image xref.

    ``insert_image`` reuses the stored image when the same pixmap is inserted
    again, which is how an authoring tool places a logo — one xref, many pages.
    """
    logo = _pixmap(200)
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page()
        if number < logo_on:
            page.insert_image(pymupdf.Rect(0, 0, 200, 200), pixmap=logo)
        if number < unique_on:
            page.insert_image(
                pymupdf.Rect(0, 250, 200, 450), pixmap=_pixmap(number + 1),
            )
    return doc


def test_grayscale_embedded_image_is_converted_to_three_channels():
    # AUDIT 2: the RGB-conversion guard checked `pixmap.n > 3`, which a
    # grayscale pixmap (n == 1) never trips, so a (H, W, 1) array reached the
    # engine and made it fail the whole page.
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 200, 200))
    gray.clear_with(128)
    doc = _pdf_with_embedded_pixmap(gray)

    arrays = page_images_to_arrays(doc[0], max_pixels=10_000_000)

    assert len(arrays) == 1
    assert arrays[0].shape == (200, 200, 3)


def test_rgb_embedded_image_is_left_as_is():
    rgb = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200))
    rgb.clear_with(128)
    doc = _pdf_with_embedded_pixmap(rgb)

    arrays = page_images_to_arrays(doc[0], max_pixels=10_000_000)

    assert len(arrays) == 1
    assert arrays[0].shape == (200, 200, 3)


def test_cmyk_embedded_image_is_still_converted():
    cmyk = pymupdf.Pixmap(pymupdf.csCMYK, pymupdf.IRect(0, 0, 200, 200))
    cmyk.clear_with(50)
    doc = _pdf_with_embedded_pixmap(cmyk)

    arrays = page_images_to_arrays(doc[0], max_pixels=10_000_000)

    assert len(arrays) == 1
    assert arrays[0].shape == (200, 200, 3)


# ---------------------------------------------------------------------------
# Figures sliced into several stored images
# ---------------------------------------------------------------------------


def _pdf_with_rects(rects, *, pixels: int = 200) -> pymupdf.Document:
    """One page carrying a distinct image in each of *rects*."""
    doc = pymupdf.open()
    page = doc.new_page()
    for number, rect in enumerate(rects):
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, pixels, pixels))
        pixmap.clear_with(number + 1)
        page.insert_image(rect, pixmap=pixmap)
    return doc


def test_stacked_slices_become_one_region():
    # The case this exists for: an authoring tool stores one diagram as
    # horizontal bands. Each band alone is unreadable — OCR must see the figure.
    strips = [pymupdf.Rect(100, 100 + 30 * i, 400, 130 + 30 * i) for i in range(6)]
    doc = _pdf_with_rects(strips)

    regions = page_image_regions(doc[0])

    assert len(regions) == 1
    assert regions[0].rect.round() == pymupdf.Rect(100, 100, 400, 280).round()


def test_figures_that_only_clip_a_corner_stay_separate():
    # Two independent figures in a busy layout overlap slightly. Merging them
    # would swallow the body text between and around them.
    doc = _pdf_with_rects([
        pymupdf.Rect(60, 170, 320, 420),
        pymupdf.Rect(270, 80, 530, 230),
    ])

    assert len(page_image_regions(doc[0])) == 2


def test_icon_sized_image_is_skipped():
    # A bullet or a decorative dot is not a figure, and dropping it before the
    # grouping keeps it from enlarging the region of a figure it sits beside.
    doc = _pdf_with_rects([pymupdf.Rect(100, 100, 400, 300)], pixels=20)

    assert page_image_regions(doc[0]) == []


def test_detailed_image_scaled_down_to_a_thumbnail_is_kept():
    # The pixels OCR needs are in the stored image, whatever size the page
    # places it at, so the filter measures the image and not its placement.
    doc = _pdf_with_rects([pymupdf.Rect(100, 100, 121, 130)], pixels=400)

    assert len(page_image_regions(doc[0])) == 1


def test_region_is_rendered_at_the_image_native_density():
    # 400 px across 200 pt is 144 DPI; rendering above that invents nothing and
    # rendering below it throws away glyphs the file holds.
    doc = _pdf_with_rects([pymupdf.Rect(0, 0, 200, 200)], pixels=400)

    arrays = page_images_to_arrays(doc[0], max_pixels=10_000_000)

    assert arrays[0].shape == (400, 400, 3)


def test_text_drawn_over_a_figure_is_part_of_the_region():
    # The callouts and legends of a technical figure are page text laid over
    # the bitmap, so pulling the image out by xref loses them.
    doc = _pdf_with_rects([pymupdf.Rect(0, 0, 200, 200)])
    page = doc[0]
    page.insert_text((20, 100), "B" * 20, fontsize=30, color=(0, 0, 0))

    arrays = page_images_to_arrays(page, max_pixels=10_000_000)

    assert arrays[0].min() < 64  # the black lettering is in the frame


# ---------------------------------------------------------------------------
# Repeated images (logos, stamps) are page furniture, not content
# ---------------------------------------------------------------------------


def test_logo_on_every_page_is_reported_as_repeated():
    doc = _pdf_with_logo(pages=10, logo_on=10)

    assert len(repeated_image_xrefs(doc)) == 1


def test_image_on_a_minority_of_pages_is_content():
    # A figure that happens to appear twice in a ten-page document is content.
    doc = _pdf_with_logo(pages=10, logo_on=2)

    assert repeated_image_xrefs(doc) == set()


def test_short_documents_are_exempt():
    # On three pages "appears on most pages" is not evidence of anything, so a
    # picture on all of them must not be mistaken for a logo.
    doc = _pdf_with_logo(pages=3, logo_on=3)

    assert repeated_image_xrefs(doc) == set()


def test_repeated_xref_is_skipped_but_page_content_is_kept():
    # The point of the skip: the logo stops reaching OCR while the picture that
    # only this page carries still does.
    doc = _pdf_with_logo(pages=10, logo_on=10, unique_on=10)
    repeated = repeated_image_xrefs(doc)

    kept = page_images_to_arrays(
        doc[0], max_pixels=10_000_000, skip_xrefs=repeated,
    )
    unfiltered = page_images_to_arrays(doc[0], max_pixels=10_000_000)

    assert len(unfiltered) == 2
    assert len(kept) == 1
