"""Input-file safety limits — the file size cap.

Guards run before heavy processing so oversized files are rejected cheaply.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default (overridable via --max-file-mb)
MAX_FILE_MB: int = 100

# The file cap measures the *compressed* size, and XLSX compresses roughly
# 10:1 — an 80 MB workbook clears 100 MB and can still expand to millions of
# cells in memory. These bound the expansion. They refuse the file rather than
# truncating it: a partial workbook is silently wrong, whereas a refusal is one
# failed file the batch reports and steps over. An OOM instead of a refusal is
# the one failure that would take every other file down with it.
#
# Both are needed. Rows alone miss a sheet that is 40 rows by 5,000 columns,
# and cells alone accept a million-row sheet one column wide.
MAX_WORKBOOK_ROWS: int = 500_000
MAX_WORKBOOK_CELLS: int = 5_000_000

# Structured readers refuse pathological expansion rather than returning partial output.
MAX_JSON_DEPTH: int = 100
MAX_JSON_CONTAINERS: int = 1_000_000
MAX_XML_DEPTH: int = 100
MAX_XML_NODES: int = 1_000_000
MAX_IMAGE_PIXELS: int = 100_000_000
MAX_TABLE_CELLS: int = 5_000_000

# Derived constant for byte comparison
MAX_FILE_BYTES: int = MAX_FILE_MB * 1024 * 1024


class WorkbookTooLargeError(Exception):
    """Raised when a workbook would expand past the in-memory row/cell caps."""

    def __init__(self, path: Path, rows: int, cells: int) -> None:
        self.path = path
        self.rows = rows
        self.cells = cells
        super().__init__(
            f"{path.name} holds {rows} rows / {cells} cells, over the limit of "
            f"{MAX_WORKBOOK_ROWS} rows / {MAX_WORKBOOK_CELLS} cells — refused "
            f"rather than truncated, so no content is silently lost"
        )


class FileTooLargeError(Exception):
    """Raised when a file exceeds the configured size cap."""

    def __init__(self, path: Path, size_bytes: int, max_bytes: int) -> None:
        self.path = path
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        size_mb = size_bytes / (1024 * 1024)
        max_mb = max_bytes / (1024 * 1024)
        super().__init__(
            f"{path.name} is {size_mb:.1f} MB, exceeds limit of {max_mb:.0f} MB"
        )


def check_file_size(path: Path, max_bytes: int | None = None) -> None:
    """Raise FileTooLargeError if *path* exceeds *max_bytes*.

    When *max_bytes* is None the module-level MAX_FILE_BYTES is used.
    """
    if max_bytes is None:
        max_bytes = MAX_FILE_BYTES
    size = path.stat().st_size
    if size > max_bytes:
        raise FileTooLargeError(path, size, max_bytes)
