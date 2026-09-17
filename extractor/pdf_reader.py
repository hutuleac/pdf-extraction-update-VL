"""PDF text + metadata + image-count extraction via PyMuPDF (FR2, FR3).

Text can optionally exclude table regions (bounding boxes from pdfplumber) so
table content is not duplicated between the text block and the table block.
"""
import logging
from pathlib import Path

import pymupdf

from extractor.headers_footers import (
    detect_headers_footers,
    detect_repeated_body_lines,
    remove_lines_from_text,
    remove_repeated_lines_from_text,
)
from extractor.model import (
    make_document,
    make_footer_block,
    make_header_block,
    make_image_block,
    make_table_block,
    make_text_block,
    make_unit,
    make_vlm_text_block,
)
from extractor.page_signals import classify_page, compute_signals, warnings_for_page
from extractor.reading_order import reorder_words
from extractor.rotated_text import drop_skewed_words, skewed_words_and_text
from extractor.table_reader import extract_tables
from extractor.url_check import check_page_urls
from extractor.vlm.apply import vlm_pages
from extractor.vlm.describe import describe_pages

logger = logging.getLogger(__name__)

# Page classes whose native text is missing or untrustworthy: the whole page is
# rasterized and OCR'd. Every other class keeps its native text untouched.
# 'mixed' and 'layout-complex' pages used to have their embedded pictures OCR'd
# too; measured on a 388-page course that produced 99k chars of axis labels and
# legend fragments (68% of tokens <=3 chars or numeric, 26% duplicating the page
# text the figure was rendered over) for no recoverable meaning. Figures are
# exported as images and read by the VLM layer instead.
FULL_PAGE_OCR_CLASSES = ("scanned", "garbled")

# Pages whose native text is fine and only the pictures may hold unread text.
# Only reached under --ocr-figures; see OcrConfig.figures for why it is opt-in.
FIGURE_OCR_CLASSES = ("mixed", "layout-complex")


def _to_word_space(page, regions) -> list[tuple]:
    """Convert pdfplumber table boxes into PyMuPDF word coordinates.

    The two libraries agree only on an upright page whose MediaBox starts at
    (0, 0). Elsewhere they diverge twice over: pdfplumber measures from the
    page's own bbox corner, and it reports a rotated page in rotated space
    while ``get_text("words")`` stays unrotated. Testing one against the other
    raw excludes the wrong words — duplicating the table into the text block
    and dropping real prose. Translating by the origin and then derotating puts
    both in the same space.
    """
    converted: list[tuple] = []
    for region in regions:
        x0, top, x1, bottom = region["bbox"]
        origin_x, origin_y = region["origin"]
        rect = pymupdf.Rect(
            x0 - origin_x, top - origin_y, x1 - origin_x, bottom - origin_y,
        )
        rect = pymupdf.Rect(rect * page.derotation_matrix).normalize()
        converted.append(tuple(rect))
    return converted


def _text_excluding_regions(page, regions, skewed_boxes=()) -> tuple[str, int, bool]:
    """Return page text with words inside any region removed, using
    column-aware reading order.

    Table *regions* are excluded by area, because a table's words sit inside it
    and nothing else does. The stamp described by *skewed_boxes* is removed word
    by word instead: it is printed diagonally *across* the body text, so
    excluding its area would delete the prose underneath it too.

    Returns (text, num_columns, concurrent_columns) where num_columns is the
    detected column count and concurrent_columns is True if the columns
    actually run side-by-side (see `reading_order._columns_run_concurrently`).
    """
    words = drop_skewed_words(page.get_text("words"), skewed_boxes)
    ordered, num_columns, concurrent_columns = reorder_words(
        words, regions_to_exclude=_to_word_space(page, regions),
    )

    # Reassemble: space within a line, newline between lines, blank line
    # between blocks. A PyMuPDF block is the closest thing the text layer has
    # to a paragraph, and the blank line is what a chunker splits on; joining
    # blocks with a bare newline made a page one run of hard-wrapped lines.
    parts: list[str] = []
    prev_key = None
    for word, block_no, line_no in ordered:
        key = (block_no, line_no)
        if prev_key is not None and key != prev_key:
            parts.append("\n" if block_no == prev_key[0] else "\n\n")
        elif parts:
            parts.append(" ")
        parts.append(word)
        prev_key = key
    return "".join(parts), num_columns, concurrent_columns


def read_document(path: Path | str, exclude_regions: dict[int, list] | None = None) -> dict:
    """Open a PDF and return metadata, per-page raw text, per-page image
    counts, per-page classification signals, and word data for header/footer
    detection.

    exclude_regions maps 1-based page number -> list of bboxes (x0, top, x1,
    bottom); words overlapping those regions are dropped from that page's text.
    """
    from extractor.ocr.raster import page_image_regions, repeated_image_xrefs

    path = Path(path)
    exclude_regions = exclude_regions or {}
    with pymupdf.open(path) as doc:
        meta = doc.metadata or {}
        # Resolved once per document, as in _extract_images and _ocr_pages: a
        # logo is not one of the page's pictures on any of them.
        repeated = repeated_image_xrefs(doc)
        page_texts: list[str] = []
        page_image_counts: list[int] = []
        page_classes: list[str] = []
        page_warnings: list[list[dict]] = []
        page_heights: list[float] = []
        page_widths: list[float] = []
        page_words: list[list[tuple]] = []
        page_column_counts: list[int] = []
        page_concurrent_columns: list[bool] = []
        page_rotated_text: list[str] = []

        for index, page in enumerate(doc):
            page_number = index + 1
            regions = exclude_regions.get(page_number)

            # A stamped watermark interleaves its glyphs with the body text, so
            # its own words are removed before reading order runs.
            try:
                rotated_boxes, rotated_text = skewed_words_and_text(page)
            except Exception as exc:  # noqa: BLE001 - never lose a page to this
                logger.warning("Rotated-text scan failed on page %d: %s", page_number, exc)
                rotated_boxes, rotated_text = [], ""
            page_rotated_text.append(rotated_text)

            try:
                text, num_columns, concurrent_columns = _text_excluding_regions(
                    page, regions or [], rotated_boxes,
                )
                page_texts.append(text)
                page_column_counts.append(num_columns)
                page_concurrent_columns.append(concurrent_columns)
            except Exception as exc:  # noqa: BLE001 - reliability: isolate page
                logger.warning("Text extraction failed on page %d: %s", page_number, exc)
                page_texts.append("")
                page_column_counts.append(1)
                page_concurrent_columns.append(False)
            try:
                # Figures, not stored images: this count is what the unit
                # reports and what decides whether a page has anything for
                # embedded-image OCR to read, and both mean "pictures a reader
                # would point at" — a diagram sliced into eight bands is one.
                page_image_counts.append(len(page_image_regions(page, skip_xrefs=repeated)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Image scan failed on page %d: %s", page_number, exc)
                page_image_counts.append(0)

            # --- page classification ---
            try:
                signals = compute_signals(page)
                page_class = classify_page(signals)
                page_classes.append(page_class)
                page_warnings.append(warnings_for_page(signals, page_class, page_number))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Page classification failed on page %d: %s", page_number, exc)
                page_classes.append("native-text")
                page_warnings.append([])

            # --- collect word data and page size for header/footer detection ---
            page_heights.append(page.rect.height)
            page_widths.append(page.rect.width)
            try:
                # Same filter as the text pass: a stamp line repeats on every
                # page and would otherwise look exactly like a header signature.
                page_words.append(
                    drop_skewed_words(page.get_text("words") or [], rotated_boxes),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Word extraction failed on page %d: %s", page_number, exc)
                page_words.append([])

        return {
            "filename": path.name,
            "pages": doc.page_count,
            "author": meta.get("author") or "",
            "title": meta.get("title") or "",
            "page_texts": page_texts,
            "page_image_counts": page_image_counts,
            "page_classes": page_classes,
            "page_warnings": page_warnings,
            "page_heights": page_heights,
            "page_widths": page_widths,
            "page_words": page_words,
            "page_column_counts": page_column_counts,
            "page_concurrent_columns": page_concurrent_columns,
            "page_rotated_text": page_rotated_text,
        }


def _min_confidence() -> float:
    """The confidence at or above which OCR text may replace garbled native text."""
    from extractor.ocr.config import get_config

    return get_config().min_confidence


# Enough of the dropped text to recognize it, without pasting a whole stamp
# into the warning.
_ROTATED_SAMPLE_CHARS = 120


def _rotated_text_warning(page_rotated_text: list[str]) -> dict | None:
    """Describe the rotated text removed from the document, if any.

    Reported rather than dropped silently: the angle test is reliable against a
    stamp but cannot know that a one-off 45 degree chart label was content, so
    the reader is told what left and can check the source.
    """
    affected = [text for text in page_rotated_text if text]
    if not affected:
        return None
    return {
        "code": "ROTATED_TEXT_FILTERED",
        "pages": len(affected),
        "characters": sum(len(text) for text in affected),
        "sample": affected[0][:_ROTATED_SAMPLE_CHARS],
    }


def _extract_images(path: Path, images_dir: Path | None) -> dict[int, list[dict]]:
    """Save each page's figures to *images_dir* and return
    ``{page_number: [image_block, ...]}``.

    One file per *figure*, not per stored image: a picture sliced into strips
    is saved whole, with the labels drawn over it (see
    ``raster.page_image_regions``). Skips page furniture (a logo or stamp
    placed on most pages — see ``repeated_image_xrefs``). Returns {} when
    *images_dir* is None, which keeps callers that don't want files on disk
    (tests, dry runs) working unchanged.
    """
    if images_dir is None:
        return {}
    from extractor.ocr.config import get_config
    from extractor.ocr.raster import page_image_regions, region_pixmap, repeated_image_xrefs

    max_pixels = get_config().max_pixels
    blocks_by_page: dict[int, list[dict]] = {}
    with pymupdf.open(path) as doc:
        skip = repeated_image_xrefs(doc)
        for index, page in enumerate(doc):
            page_number = index + 1
            counter = 0
            for region in page_image_regions(page, skip_xrefs=skip):
                try:
                    pixmap = region_pixmap(page, region, max_pixels=max_pixels)
                except Exception as exc:  # noqa: BLE001 - one bad figure must not stop the page
                    logger.warning("Image extraction failed on page %d: %s", page_number, exc)
                    continue
                counter += 1
                filename = f"p{page_number:03d}_i{counter:02d}.png"
                images_dir.mkdir(parents=True, exist_ok=True)
                (images_dir / filename).write_bytes(pixmap.tobytes("png"))
                blocks_by_page.setdefault(page_number, []).append(make_image_block(
                    f"{images_dir.name}/{filename}",
                    width=pixmap.width, height=pixmap.height,
                ))
    return blocks_by_page


def _vlm_candidates(page_classes: list[str], page_warnings: list[list[dict]]) -> frozenset[int]:
    """Pages whose text layer the visual model can repair: missing (scanned),
    untrusted (garbled), or sound prose with symbol-font damage in its formulas
    (MISMAPPED_GLYPHS). Everywhere else the native text is already correct."""
    return frozenset(
        index + 1
        for index, (cls, warnings) in enumerate(zip(page_classes, page_warnings, strict=True))
        if cls in FULL_PAGE_OCR_CLASSES
        or any(w["code"] == "MISMAPPED_GLYPHS" for w in warnings)
    )


def _ocr_pages(
    path: Path,
    page_classes: list[str],
    skip: set[int] | None = None,
    page_image_counts: list[int] | None = None,
) -> dict[int, tuple[dict | None, list[dict]]]:
    """OCR the pages whose native text is missing or untrustworthy.

    Keyed by 1-based page number; empty when no page needs OCR. When OCR
    cannot run, each affected page still gets a warning explaining why, so an
    unreadable page is never silently empty.

    *skip* holds pages the visual model already read. Both paths replace the
    same untrusted native text, so running OCR there would produce a second
    recovery block for one page — the duplication this pipeline exists to
    avoid — and cost a rasterization for text that is thrown away.

    Under ``OcrConfig.figures`` the pictures on a ``FIGURE_OCR_CLASSES`` page
    are read too, but only where *page_image_counts* says the page has one —
    rasterizing a page with no figure is wasted work.
    """
    from extractor.ocr import registry
    from extractor.ocr.apply import ocr_images, unavailable_warning
    from extractor.ocr.config import get_config
    from extractor.ocr.raster import (
        page_images_to_arrays,
        page_to_array,
        repeated_image_xrefs,
    )

    config = get_config()
    skip = skip or set()
    counts = page_image_counts or [0] * len(page_classes)
    figures = config.figures and page_image_counts is not None
    targets = [
        i + 1 for i, cls in enumerate(page_classes)
        if i + 1 not in skip
        and (
            cls in FULL_PAGE_OCR_CLASSES
            or (figures and cls in FIGURE_OCR_CLASSES and counts[i] > 0)
        )
    ]
    if not targets:
        return {}
    if not registry.is_available():
        return {page: (None, [unavailable_warning(page)]) for page in targets}

    results: dict[int, tuple[dict | None, list[dict]]] = {}
    with pymupdf.open(path) as doc:
        # Page furniture is a property of the document, so this is resolved
        # once: a logo skipped on page 2 but read on page 40 would put the same
        # noise back into the output.
        repeated = repeated_image_xrefs(doc) if figures else set()
        for page_number in targets:
            page = doc[page_number - 1]
            try:
                if page_classes[page_number - 1] in FULL_PAGE_OCR_CLASSES:
                    images = [page_to_array(
                        page, dpi=config.dpi, max_pixels=config.max_pixels,
                    )]
                else:
                    images = page_images_to_arrays(
                        page, max_pixels=config.max_pixels, skip_xrefs=repeated,
                    )
            except Exception as exc:  # noqa: BLE001 - isolate one page
                logger.warning("Rasterization failed on page %d: %s", page_number, exc)
                results[page_number] = (
                    None, [{"code": "OCR_FAILED", "page": page_number, "detail": str(exc)}],
                )
                continue
            results[page_number] = ocr_images(images, page_number)
    return results


def extract_pdf(path: Path | str, *, images_dir: Path | str | None = None) -> dict:
    """Extract a PDF into the internal model.

    Runs table detection first, excludes table regions from the page text to
    avoid duplicating cell content, then assembles one unit per page with
    page classification, header/footer detection, and warnings.

    *images_dir*, when given, is where embedded page images are saved; image
    blocks then carry paths relative to *images_dir*'s parent (where the
    Markdown/JSON output lives). Without it, images are still counted but not
    extracted — the pre-existing behavior.
    """
    path = Path(path)
    images_dir = Path(images_dir) if images_dir is not None else None
    tables = extract_tables(path)
    # The bbox travels with its page origin: converting it to word coordinates
    # needs both, plus the page itself (see _to_word_space).
    exclude_regions = {
        page: [{"bbox": entry["bbox"], "origin": entry["origin"]} for entry in entries]
        for page, entries in tables.items()
    }
    reader_data = read_document(path, exclude_regions=exclude_regions)

    # --- header/footer detection ---
    hf_result = detect_headers_footers(
        reader_data["page_texts"],
        reader_data["page_heights"],
        reader_data["page_words"],
        reader_data["page_widths"],
    )
    chrome_lines_by_page = detect_repeated_body_lines(reader_data["page_texts"])

    # The visual model runs first: on a page whose native text is untrusted its
    # reading supersedes OCR's, so OCR skips the pages it already covered.
    vlm_by_page, vlm_warnings = vlm_pages(
        path,
        reader_data["page_classes"],
        reader_data["page_texts"],
        # Pages that map to an empty list are pages pdfplumber found nothing
        # on, where the model's tables are still taken.
        frozenset(page for page, entries in tables.items() if entries),
        candidates=_vlm_candidates(reader_data["page_classes"], reader_data["page_warnings"]),
    )
    # Only a reading that actually carries text displaces OCR. A page accepted
    # for its tables alone replaces nothing, and skipping OCR there would leave
    # the garbled characters standing with no second attempt at them.
    vlm_replaced = {
        page for page, parsed in vlm_by_page.items()
        if reader_data["page_classes"][page - 1] in FULL_PAGE_OCR_CLASSES
        and parsed.text.strip()
    }
    # Independent of the reading pass: a different model answering a different
    # question, on the figure-bearing pages the reading pass has no answer for.
    figure_by_page, describe_warnings = describe_pages(
        path, reader_data["page_classes"], reader_data["page_image_counts"],
    )
    ocr_by_page = _ocr_pages(
        path,
        reader_data["page_classes"],
        skip=vlm_replaced,
        page_image_counts=reader_data["page_image_counts"],
    )
    image_blocks_by_page = _extract_images(path, images_dir)

    total_images = sum(reader_data["page_image_counts"])
    units: list[dict] = []
    doc_warnings: list[dict] = list(vlm_warnings) + describe_warnings

    rotated_warning = _rotated_text_warning(reader_data["page_rotated_text"])
    if rotated_warning:
        doc_warnings.append(rotated_warning)

    if hf_result.detected or chrome_lines_by_page:
        doc_warnings.append({"code": "HEADER_FOOTER_DETECTED"})

    paired = zip(
        reader_data["page_texts"],
        reader_data["page_image_counts"],
        reader_data["page_classes"],
        reader_data["page_warnings"],
        reader_data["page_column_counts"],
        reader_data["page_concurrent_columns"],
        strict=True,
    )
    for index, (raw_text, img_count, page_class, warnings, num_columns, concurrent_columns) in enumerate(paired):
        page_number = index + 1
        blocks: list[dict] = []

        # Two-column warning: only when the columns actually run side-by-side
        # (see reading_order._columns_run_concurrently) — a column split with
        # no vertical overlap reads correctly top-to-bottom regardless of
        # order, so warning about it would just be noise.
        if num_columns > 1 and concurrent_columns:
            doc_warnings.append({"code": "POSSIBLE_TWO_COLUMN_ORDER", "page": page_number})

        # Emit header blocks (if detected) before text
        header_lines = hf_result.headers_by_page.get(page_number, [])
        for line in header_lines:
            hblock = make_header_block(line)
            if hblock:
                blocks.append(hblock)

        # Remove detected header/footer lines from the text content
        cleaned_text = raw_text
        footer_lines = hf_result.footers_by_page.get(page_number, [])
        if header_lines or footer_lines:
            cleaned_text = remove_lines_from_text(cleaned_text, header_lines, footer_lines)
        side_lines = hf_result.side_lines_by_page.get(page_number, [])
        chrome_lines = chrome_lines_by_page.get(page_number, [])
        if side_lines or chrome_lines:
            cleaned_text = remove_repeated_lines_from_text(
                cleaned_text, side_lines + chrome_lines,
            )

        text_block = make_text_block(cleaned_text)
        ocr_block, ocr_warnings = ocr_by_page.get(page_number, (None, []))
        doc_warnings.extend(ocr_warnings)
        parsed = vlm_by_page.get(page_number)
        vlm_block = make_vlm_text_block(parsed.text) if parsed else None

        # On a page whose native text is untrusted the visual reading replaces
        # it, as OCR does below and for the same reason; everywhere else the
        # native text is correct and the model's output is added beside it,
        # because what it contributes there is formulas and figure structure
        # the native path cannot see.
        if vlm_block and page_class in FULL_PAGE_OCR_CLASSES:
            text_block = vlm_block
            vlm_block = None

        # 'garbled' pages have native text that cannot be trusted: OCR replaces
        # it, but only when the recognition is confident enough to be better.
        if ocr_block and page_class == "garbled":
            if ocr_block["confidence"] >= _min_confidence():
                text_block = ocr_block
            else:
                # The text was read but not trusted enough to publish. Leaving
                # OCR_APPLIED here would promise content the page does not hold,
                # which stops the reader going back to the source.
                doc_warnings = [
                    w for w in doc_warnings
                    if not (w["code"] == "OCR_APPLIED" and w.get("page") == page_number)
                ]
                doc_warnings.append({
                    "code": "OCR_REJECTED_LOW_CONFIDENCE",
                    "page": page_number,
                    "confidence": ocr_block["confidence"],
                })
            ocr_block = None

        if text_block:
            blocks.append(text_block)
        if ocr_block:
            blocks.append(ocr_block)
        if vlm_block:
            blocks.append(vlm_block)
        # After the page's own text, because it describes what the page shows
        # rather than what it says — a reader (or a chunker) wants the source
        # first and the commentary on it second.
        figure_block = make_vlm_text_block(
            figure_by_page.get(page_number, ""), source="vlm-figure",
        )
        if figure_block:
            blocks.append(figure_block)

        doc_warnings.extend(check_page_urls(blocks, page_number))

        native_tables = tables.get(page_number, [])
        for table in native_tables:
            blocks.append(make_table_block(table["cells"]))
        # Native tables are authoritative — pdfplumber reads the ruling lines,
        # the model infers them. Its tables are only taken where there are none.
        if parsed and not native_tables:
            for rows in parsed.tables:
                blocks.append(make_table_block(rows, source="vlm"))

        # ponytail: appended after text/tables rather than interleaved at
        # their true position on the page — upgrade to position-based
        # ordering (reading_order.py) if reading order across a figure
        # matters more than knowing it exists.
        blocks.extend(image_blocks_by_page.get(page_number, []))

        # Emit footer blocks after text/tables
        for line in footer_lines:
            fblock = make_footer_block(line)
            if fblock:
                blocks.append(fblock)

        units.append(make_unit(
            page_number, "page", blocks, image_count=img_count, page_class=page_class,
        ))
        doc_warnings.extend(warnings)

    return make_document(
        reader_data["filename"], "pdf", reader_data["pages"], units,
        author=reader_data["author"], title=reader_data["title"],
        image_count=total_images,
        warnings=doc_warnings or None,
    )
