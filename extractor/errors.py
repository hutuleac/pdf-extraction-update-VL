"""Stable categories for expected extraction failures."""
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib
from enum import StrEnum
from pathlib import Path

from extractor.limits import FileTooLargeError, WorkbookTooLargeError


class FatalErrorCategory(StrEnum):
    """User-visible categories for failures that refuse one input file."""

    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    ENCRYPTED = "encrypted"
    RESOURCE_LIMIT = "resource_limit"
    MISSING_PART = "missing_part"
    IO = "io"


class ExtractionError(Exception):
    """An expected extraction failure with a stable category."""

    def __init__(
        self,
        category: FatalErrorCategory,
        message: str,
        path: Path | None = None,
    ) -> None:
        self.category = category
        self.path = path
        super().__init__(message)


class ResourceLimitError(ExtractionError):
    """A bounded reader would exceed an expansion budget."""

    def __init__(self, limit_name: str, observed: int, allowed: int) -> None:
        self.limit_name = limit_name
        self.observed = observed
        self.allowed = allowed
        super().__init__(
            FatalErrorCategory.RESOURCE_LIMIT,
            f"{limit_name} is {observed}, exceeds limit of {allowed}; refused "
            "rather than returning partial content",
        )


def _path_from_exception(exc: Exception) -> Path | None:
    path = getattr(exc, "path", None)
    return path if isinstance(path, Path) else None


def _package_category(exc: Exception) -> FatalErrorCategory:
    message = str(exc).lower()
    if "encrypted" in message or "password" in message:
        return FatalErrorCategory.ENCRYPTED
    if "missing" in message or "not found" in message or "no such file" in message:
        return FatalErrorCategory.MISSING_PART
    return FatalErrorCategory.MALFORMED


def classify_exception(exc: Exception) -> ExtractionError:
    """Convert known operational failures, re-raising programming errors."""
    if isinstance(exc, ExtractionError):
        return exc
    if isinstance(exc, ResourceLimitError):
        return exc
    if isinstance(exc, (FileTooLargeError, WorkbookTooLargeError)):
        return ExtractionError(FatalErrorCategory.RESOURCE_LIMIT, str(exc), _path_from_exception(exc))
    if isinstance(exc, ValueError) and str(exc).startswith("Unsupported file type:"):
        return ExtractionError(FatalErrorCategory.UNSUPPORTED, str(exc))
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return ExtractionError(FatalErrorCategory.IO, str(exc), _path_from_exception(exc))
    if isinstance(exc, (zipfile.BadZipFile, zlib.error, ElementTree.ParseError, EOFError)):
        return ExtractionError(_package_category(exc), str(exc), _path_from_exception(exc))
    if isinstance(exc, OSError):
        return ExtractionError(FatalErrorCategory.IO, str(exc), _path_from_exception(exc))
    raise exc
