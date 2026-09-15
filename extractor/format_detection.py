"""Bounded validation for formats with strong file signatures."""
import xml.etree.ElementTree as ElementTree
import zlib
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from extractor.errors import ExtractionError, FatalErrorCategory

_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_OOXML_CONTENT_TYPES = {
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    },
    ".xlsm": {
        "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    },
}


def _failure(category: FatalErrorCategory, path: Path, message: str) -> ExtractionError:
    return ExtractionError(category, f"{path.name}: {message}", path)


def detect_package_kind(path: Path) -> str | None:
    """Return a strong signature kind, or None for ambiguous/plain formats."""
    with path.open("rb") as stream:
        prefix = stream.read(512)
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(b"{\\rtf"):
        return "rtf"
    if prefix.startswith(_OLE_SIGNATURE):
        return "ole"
    if prefix.startswith(b"PK"):
        return {
            ".docx": "docx",
            ".xlsx": "xlsx",
            ".xlsm": "xlsx",
            ".pptx": "pptx",
        }.get(path.suffix.lower())
    return None


def _content_types(path: Path) -> set[str]:
    try:
        with ZipFile(path) as package:
            raw = package.read("[Content_Types].xml")
    except (BadZipFile, zlib.error) as exc:
        raise _failure(FatalErrorCategory.MALFORMED, path, "invalid ZIP package") from exc
    except KeyError as exc:
        raise _failure(FatalErrorCategory.MISSING_PART, path, "missing [Content_Types].xml") from exc
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise _failure(FatalErrorCategory.MALFORMED, path, "invalid [Content_Types].xml") from exc
    return {
        element.attrib["ContentType"]
        for element in root
        if "ContentType" in element.attrib
    }


def validate_path(path: Path, declared_suffix: str) -> None:
    """Validate strong signatures for the extension-selected reader."""
    suffix = declared_suffix.lower()
    kind = detect_package_kind(path)
    if suffix == ".pdf":
        if kind != "pdf":
            raise _failure(FatalErrorCategory.MALFORMED, path, "missing PDF signature")
        return
    if suffix == ".rtf":
        if kind != "rtf":
            raise _failure(FatalErrorCategory.MALFORMED, path, "missing RTF signature")
        return
    expected = _OOXML_CONTENT_TYPES.get(suffix)
    if expected is None:
        return
    if kind != "xlsx" and kind != suffix[1:]:
        raise _failure(FatalErrorCategory.MALFORMED, path, "missing OOXML ZIP signature")
    content_types = _content_types(path)
    if not content_types & expected:
        raise _failure(FatalErrorCategory.MALFORMED, path, "unexpected OOXML main-document type")
