"""Column-aware reading order for PDF pages.

Groups words into lines, lines into blocks, clusters blocks into columns by
x-overlap, then orders columns left-to-right and blocks top-to-bottom within
each column.

Single-column pages produce the same order as PyMuPDF's default (block_no,
line_no) ordering — this is the regression guard.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from extractor.geometry import rects_overlap

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Minimum horizontal overlap fraction between two blocks to consider them in the
# same column. Two blocks overlap if the overlap width / min(block_width) > this.
COLUMN_OVERLAP_THRESHOLD = 0.40

# Minimum gap (in points) between column centres to treat them as distinct columns.
MIN_COLUMN_GAP = 50.0

# A block spanning at least this fraction of the text width is a banner (title,
# rule, section heading, footer), not a column. Letting one join a column merges
# every column it crosses — the greedy clustering grows that column's span to
# the full page width and swallows the rest, which is exactly the title-over-
# two-columns layout that most reports use.
FULL_WIDTH_FRACTION = 0.8


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WordInfo:
    """A single word with its bounding box and structural indices."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_no: int
    line_no: int


@dataclass
class Block:
    """A group of words sharing (block_no) that forms a visual block."""

    block_no: int
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    words: list[WordInfo] = field(default_factory=list)

    @property
    def centre_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _words_to_blocks(words: list[WordInfo]) -> list[Block]:
    """Group words by block_no and compute bounding boxes."""
    blocks_map: dict[int, Block] = {}
    for w in words:
        if w.block_no not in blocks_map:
            blocks_map[w.block_no] = Block(
                block_no=w.block_no, x0=w.x0, y0=w.y0, x1=w.x1, y1=w.y1,
            )
        blk = blocks_map[w.block_no]
        blk.x0 = min(blk.x0, w.x0)
        blk.y0 = min(blk.y0, w.y0)
        blk.x1 = max(blk.x1, w.x1)
        blk.y1 = max(blk.y1, w.y1)
        blk.words.append(w)
    return list(blocks_map.values())


def _blocks_overlap_x(a: Block, b: Block) -> bool:
    """True if blocks a and b share significant horizontal overlap."""
    overlap_start = max(a.x0, b.x0)
    overlap_end = min(a.x1, b.x1)
    overlap_width = max(0.0, overlap_end - overlap_start)
    min_width = min(a.width, b.width)
    if min_width <= 0:
        return True  # degenerate block, cluster together
    return (overlap_width / min_width) > COLUMN_OVERLAP_THRESHOLD


def _cluster_into_columns(blocks: list[Block]) -> list[list[Block]]:
    """Cluster blocks into columns by x-overlap using a greedy merge.

    Returns a list of columns, each a list of blocks, sorted left-to-right
    by column centre. Blocks within each column are sorted top-to-bottom.
    """
    if not blocks:
        return []

    # Sort blocks by x0 for deterministic clustering
    sorted_blocks = sorted(blocks, key=lambda b: (b.x0, b.y0))

    columns: list[list[Block]] = []
    for block in sorted_blocks:
        placed = False
        for col in columns:
            # Check overlap with the column's representative range
            col_x0 = min(b.x0 for b in col)
            col_x1 = max(b.x1 for b in col)
            col_width = col_x1 - col_x0
            # Create a synthetic block representing the column span
            overlap_start = max(block.x0, col_x0)
            overlap_end = min(block.x1, col_x1)
            overlap_width = max(0.0, overlap_end - overlap_start)
            min_width = min(block.width, col_width) if col_width > 0 else block.width
            if min_width > 0 and (overlap_width / min_width) > COLUMN_OVERLAP_THRESHOLD:
                col.append(block)
                placed = True
                break
        if not placed:
            columns.append([block])

    # Sort columns left-to-right by average centre
    columns.sort(key=lambda col: sum(b.centre_x for b in col) / len(col))

    # Sort blocks within each column top-to-bottom
    for col in columns:
        col.sort(key=lambda b: b.y0)

    return columns


def _is_full_width(block: Block, page_span: float) -> bool:
    """True if the block spans most of the text width, so it is a banner."""
    return page_span > 0 and block.width / page_span >= FULL_WIDTH_FRACTION


def _columns_run_concurrently(columns: list[list[Block]]) -> bool:
    """True if two or more columns actually run side-by-side down the page.

    A column split can come from noise (a stray block the overlap threshold
    happened to isolate) rather than genuine side-by-side text. If the
    "columns" don't overlap vertically — one occupies the top of the band,
    the other the bottom — there is no interleaving question to get wrong:
    reading them top-to-bottom in column order already matches the only
    sensible order. Only concurrent columns (their y-ranges overlap) create
    the ambiguity that POSSIBLE_TWO_COLUMN_ORDER exists to flag.
    """
    if len(columns) < 2:
        return False
    spans = [(min(b.y0 for b in col), max(b.y1 for b in col)) for col in columns]
    spans.sort()
    return any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))


def _order_in_bands(blocks: list[Block]) -> tuple[list[Block], int, bool]:
    """Order blocks in reading order, treating full-width blocks as banners.

    Each banner closes the band above it and opens the one below, so a page
    reads as: blocks above the first banner, then banner, then the blocks it
    introduces, and so on. Within a band the normal column clustering applies.
    This handles both a title above two columns and a full-width section
    heading part-way down the page.

    Returns (ordered_blocks, num_columns, concurrent), where num_columns is
    the widest band's column count and concurrent is True if any band's
    columns actually run side-by-side (see `_columns_run_concurrently`) —
    together these drive POSSIBLE_TWO_COLUMN_ORDER, and a page whose columns
    sit under a title is still a two-column page.
    """
    page_span = max(b.x1 for b in blocks) - min(b.x0 for b in blocks)
    banners = sorted(
        (b for b in blocks if _is_full_width(b, page_span)), key=lambda b: b.y0,
    )
    if not banners:
        columns = _cluster_into_columns(blocks)
        return (
            [b for col in columns for b in col],
            len(columns),
            _columns_run_concurrently(columns),
        )

    body = [b for b in blocks if not _is_full_width(b, page_span)]
    # Band boundaries: everything above the first banner, then one band per
    # banner running to the next banner's top edge.
    edges = [b.y0 for b in banners]
    ordered: list[Block] = []
    num_columns = 1
    concurrent = False

    def emit(band: list[Block]) -> None:
        nonlocal num_columns, concurrent
        if not band:
            return
        columns = _cluster_into_columns(band)
        num_columns = max(num_columns, len(columns))
        concurrent = concurrent or _columns_run_concurrently(columns)
        ordered.extend(b for col in columns for b in col)

    emit([b for b in body if b.y0 < edges[0]])
    for i, banner in enumerate(banners):
        ordered.append(banner)
        upper = edges[i + 1] if i + 1 < len(edges) else float("inf")
        emit([b for b in body if edges[i] <= b.y0 < upper])

    return ordered, num_columns, concurrent


def _reassemble_block_text(block: Block) -> list[tuple[str, int, int]]:
    """Return words from a block sorted by line then x position.

    Returns list of (word_text, block_no, line_no) preserving line structure.
    """
    # Group by line_no, sort each line by x0
    lines: dict[int, list[WordInfo]] = {}
    for w in block.words:
        lines.setdefault(w.line_no, []).append(w)

    result: list[tuple[str, int, int]] = []
    for line_no in sorted(lines):
        for w in sorted(lines[line_no], key=lambda w: w.x0):
            result.append((w.text, block.block_no, line_no))
    return result


def reorder_words(
    words: list[tuple],
    regions_to_exclude: list | None = None,
) -> tuple[list[tuple[str, int, int]], int, bool]:
    """Reorder page words using column-aware reading order.

    Parameters:
      words: raw word tuples from page.get_text("words"):
             (x0, y0, x1, y1, word, block_no, line_no, word_no)
      regions_to_exclude: optional list of bbox tuples to exclude

    Returns:
      (ordered_words, num_columns, concurrent_columns) where ordered_words is
      a list of (word_text, block_no, line_no) tuples in column-aware reading
      order, num_columns is the detected column count, and concurrent_columns
      is True if two or more columns actually run side-by-side (see
      `_columns_run_concurrently`) — the signal for whether the column split
      creates real reading-order ambiguity worth warning about.
    """
    regions_to_exclude = regions_to_exclude or []

    # Filter out words in excluded regions
    filtered: list[WordInfo] = []
    for x0, y0, x1, y1, word, block_no, line_no, _word_no in words:
        if regions_to_exclude and any(
            rects_overlap((x0, y0, x1, y1), r) for r in regions_to_exclude
        ):
            continue
        filtered.append(WordInfo(
            x0=x0, y0=y0, x1=x1, y1=y1,
            text=word, block_no=block_no, line_no=line_no,
        ))

    if not filtered:
        return [], 1, False

    # Group into blocks, then order them band by band around any banners
    blocks = _words_to_blocks(filtered)
    ordered_blocks, num_columns, concurrent = _order_in_bands(blocks)

    ordered: list[tuple[str, int, int]] = []
    for block in ordered_blocks:
        ordered.extend(_reassemble_block_text(block))

    return ordered, num_columns, concurrent
