"""Route each input file to its format reader by extension.

Every reader returns the same internal model, so downstream writers and phase 2
are format-agnostic. Add a new format by writing a reader and registering it in
SUPPORTED_EXTENSIONS.
"""
from pathlib import Path

from extractor.csv_reader import extract_csv
from extractor.docx_reader import extract_docx
from extractor.format_detection import validate_path
from extractor.html_reader import extract_html
from extractor.image_reader import IMAGE_EXTENSIONS, extract_image
from extractor.json_reader import extract_json
from extractor.md_reader import extract_md
from extractor.pdf_reader import extract_pdf
from extractor.pptx_reader import extract_pptx
from extractor.txt_reader import extract_txt
from extractor.xlsx_reader import extract_xlsx
from extractor.xml_reader import extract_xml

SUPPORTED_EXTENSIONS = {
    ".pdf": extract_pdf,
    ".csv": extract_csv,
    ".docx": extract_docx,
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    ".txt": extract_txt,
    ".md": extract_md,
    ".html": extract_html,
    ".htm": extract_html,
    ".pptx": extract_pptx,
    ".json": extract_json,
    ".xml": extract_xml,
    # Image files carry no native text; the reader defers every OCR import to
    # call time, so a machine without OCR dependencies still imports cleanly.
    **{extension: extract_image for extension in IMAGE_EXTENSIONS},
}


def extract_document(path: Path | str, *, images_dir: Path | str | None = None) -> dict:
    """Extract any supported file into the internal model.

    *images_dir* is where a PDF's embedded images are saved; every other
    reader ignores it, so passing it always is harmless.

    Raises ValueError if the extension is not supported.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    reader = SUPPORTED_EXTENSIONS.get(suffix)
    if reader is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    validate_path(path, suffix)
    if suffix == ".pdf":
        return reader(path, images_dir=images_dir)
    return reader(path)
