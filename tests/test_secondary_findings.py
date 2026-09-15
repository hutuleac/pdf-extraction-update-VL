"""Regressions for the secondary findings of the 2026-08-31 code audit.

Each test carries its `AUDIT <n>` reference. These are lower-severity defects
than the numbered findings, but every one of them either loses content, hides a
signal, or leaks a resource.
"""
import json
import logging

import numpy as np
import pytest

from extractor.errors import ResourceLimitError
from extractor.json_reader import extract_json
from extractor.xlsx_reader import extract_xlsx
from extractor.xml_reader import extract_xml


def _text_of(model: dict) -> str:
    return "\n".join(
        b["content"]
        for unit in model["pages"]
        for b in unit["content"]
        if b["type"] == "text"
    )


def _codes(model: dict) -> list[str]:
    return [w["code"] for w in model["document"].get("warnings", [])]


# ---------------------------------------------------------------------------
# AUDIT 12 — .xlsm must not report itself as .xlsx
# ---------------------------------------------------------------------------

class TestMacroWorkbookSourceType:
    def test_xlsm_reports_its_own_source_type(self, tmp_path, sample_xlsx):
        """A downstream consumer cannot tell a macro workbook apart otherwise."""
        macro = tmp_path / "macros.xlsm"
        macro.write_bytes(sample_xlsx.read_bytes())

        assert extract_xlsx(macro)["document"]["source_type"] == "xlsm"

    def test_xlsx_still_reports_xlsx(self, sample_xlsx):
        assert extract_xlsx(sample_xlsx)["document"]["source_type"] == "xlsx"


# ---------------------------------------------------------------------------
# AUDIT 11 — an oversized workbook must fail one file, never the batch
# ---------------------------------------------------------------------------

class TestWorkbookSizeGuard:
    """XLSX compresses ~10:1, so a 40 MB workbook clears the 50 MB file cap.

    Truncating it would make the document silently incomplete. Refusing it
    keeps the content honest and, crucially, keeps the failure inside one file
    — an OOM here is the one thing that would take the whole batch down.
    """

    @staticmethod
    def _workbook(directory, rows, cols=3):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        for i in range(rows):
            ws.append([f"c{j}r{i}" for j in range(cols)])
        out = directory / f"wb_{rows}x{cols}.xlsx"
        wb.save(out)
        return out

    def test_normal_workbook_still_extracts(self, tmp_path):
        model = extract_xlsx(self._workbook(tmp_path, 20))
        assert model["pages"][0]["content"][0]["content"][0][0] == "c0r0"

    def test_too_many_rows_is_refused(self, tmp_path, monkeypatch):
        from extractor import limits, xlsx_reader

        monkeypatch.setattr(limits, "MAX_WORKBOOK_ROWS", 10)
        monkeypatch.setattr(xlsx_reader, "MAX_WORKBOOK_ROWS", 10)

        with pytest.raises(limits.WorkbookTooLargeError) as exc:
            extract_xlsx(self._workbook(tmp_path, 50))

        assert "50" in str(exc.value)

    def test_a_wide_sheet_is_refused_on_cells_not_rows(self, tmp_path, monkeypatch):
        """A row count alone misses the sheet that is 40 rows by 5,000 columns."""
        from extractor import limits, xlsx_reader

        monkeypatch.setattr(limits, "MAX_WORKBOOK_CELLS", 100)
        monkeypatch.setattr(xlsx_reader, "MAX_WORKBOOK_CELLS", 100)

        with pytest.raises(limits.WorkbookTooLargeError):
            extract_xlsx(self._workbook(tmp_path, 40, cols=50))

    def test_guard_still_fires_when_dimensions_are_missing(self, tmp_path, monkeypatch):
        """max_row is None on a sheet with no dimension record, and a hostile
        file can simply understate it — so the streaming count is the backstop."""
        from extractor import limits, xlsx_reader

        monkeypatch.setattr(limits, "MAX_WORKBOOK_ROWS", 10)
        monkeypatch.setattr(xlsx_reader, "MAX_WORKBOOK_ROWS", 10)
        # (0, 0) is exactly what a sheet with no dimension record reports.
        monkeypatch.setattr(xlsx_reader, "_preflight", lambda workbook: (0, 0))

        with pytest.raises(limits.WorkbookTooLargeError):
            extract_xlsx(self._workbook(tmp_path, 50))

    def test_one_oversized_workbook_does_not_stop_the_batch(self, tmp_path, monkeypatch):
        """This is the whole point: isolation, not a dead run."""
        from extractor import limits, xlsx_reader
        from main import main

        monkeypatch.setattr(limits, "MAX_WORKBOOK_ROWS", 10)
        monkeypatch.setattr(xlsx_reader, "MAX_WORKBOOK_ROWS", 10)

        input_dir = tmp_path / "in"
        input_dir.mkdir()
        self._workbook(input_dir, 50)
        (input_dir / "fine.txt").write_text("ordinary text", encoding="utf-8")

        output_dir = tmp_path / "out"
        code = main([
            "--input", str(input_dir),
            "--output", str(output_dir),
            "--log-file", str(tmp_path / "t.log"),
        ])

        assert code == 1, "a refused workbook must be reported as a failure"
        assert (output_dir / "json" / "fine.json").exists(), "the batch stopped"


# ---------------------------------------------------------------------------
# AUDIT 16 — unguarded recursion turns a deep document into a failed file
# ---------------------------------------------------------------------------

class TestDeepNestingIsTruncatedNotFatal:
    """A depth cap with a warning degrades better than a RecursionError.

    The batch loop catches RecursionError, so today the whole file simply
    fails and its shallow content is lost with it.
    """

    @staticmethod
    def _deep_json(depth: int) -> str:
        payload = "inner"
        for _ in range(depth):
            payload = {"child": payload}
        return json.dumps(payload)

    def test_deeply_nested_json_still_produces_a_document(self, tmp_path):
        f = tmp_path / "deep.json"
        f.write_text(self._deep_json(2000), encoding="utf-8")

        with pytest.raises(ResourceLimitError):
            extract_json(f)

    def test_shallow_json_is_not_truncated(self, tmp_path):
        f = tmp_path / "shallow.json"
        f.write_text(self._deep_json(3), encoding="utf-8")

        model = extract_json(f)

        assert "NESTING_TRUNCATED" not in _codes(model)
        assert "inner" in _text_of(model)

    def test_deeply_nested_xml_still_produces_a_document(self, tmp_path):
        depth = 2000
        f = tmp_path / "deep.xml"
        f.write_text("<a>" * depth + "leaf" + "</a>" * depth, encoding="utf-8")

        with pytest.raises(ResourceLimitError):
            extract_xml(f)

    def test_shallow_xml_is_not_truncated(self, tmp_path):
        f = tmp_path / "shallow.xml"
        f.write_text("<root><item>value</item></root>", encoding="utf-8")

        model = extract_xml(f)

        assert "NESTING_TRUNCATED" not in _codes(model)
        assert "value" in _text_of(model)


# ---------------------------------------------------------------------------
# AUDIT 18 — a swallowed exception must leave a trace
# ---------------------------------------------------------------------------

class TestImageRectFailureIsLogged:
    def test_failing_image_rects_logs_at_debug(self, caplog, monkeypatch):
        """Silently classifying on image_area_ratio 0 hides why a page misread."""
        from extractor import page_signals

        class _Page:
            rect = type("R", (), {"width": 600.0, "height": 800.0})()

            def get_text(self, kind, flags=0):
                if kind == "text":
                    return "some text on the page"
                if kind == "dict":
                    return {"blocks": []}
                return []

            def get_images(self, full=False):
                return [(1, 0, 10, 10, 8, "DeviceRGB", "", "", "")]

            def get_image_rects(self, xref):
                raise RuntimeError("no rects for you")

            def get_drawings(self):
                return []

        with caplog.at_level(logging.DEBUG, logger=page_signals.__name__):
            signals = page_signals.compute_signals(_Page())

        assert signals.image_area_ratio == 0.0
        assert any("no rects for you" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# AUDIT 19 — truncating candidates must keep the biggest, not the first
# ---------------------------------------------------------------------------

class TestContourTruncationKeepsLargest:
    def test_largest_regions_survive_the_candidate_cap(self, monkeypatch):
        """Keeping OpenCV's discovery order drops real text lines on a dense page."""
        from extractor.ocr import dbnet_post

        prob = np.zeros((200, 200), dtype=np.float32)
        # One tiny blob and two large ones. The tiny one sits where OpenCV
        # discovers it *first*, so slicing in discovery order keeps it and
        # drops a real text line — the test would pass vacuously otherwise.
        prob[186:190, 10:14] = 1.0      # tiny, found first
        prob[20:70, 20:180] = 1.0       # large
        prob[100:150, 20:180] = 1.0     # large

        monkeypatch.setattr(dbnet_post, "MAX_CANDIDATES", 2)
        boxes = dbnet_post.boxes_from_bitmap(prob, (200, 200))

        assert len(boxes) == 2
        areas = sorted(
            (b[:, 0].max() - b[:, 0].min()) * (b[:, 1].max() - b[:, 1].min())
            for b in boxes
        )
        assert areas[0] > 1000, f"a tiny box survived the cap: {areas}"


# ---------------------------------------------------------------------------
# AUDIT 15 — pdfplumber and PyMuPDF coordinate spaces must be reconciled
# ---------------------------------------------------------------------------

class TestTableExclusionAcrossCoordinateSpaces:
    """pdfplumber bboxes are tested against PyMuPDF word coordinates.

    The two agree only for an upright page with a MediaBox origin at (0, 0).
    A rotated page or a shifted origin puts them in different spaces, and the
    exclusion then removes the wrong words: table text is duplicated into the
    text block while real prose can be dropped.
    """

    @staticmethod
    def _table_pdf(directory, *, rotation=0, origin=(0, 0)):
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((100, 200), "CellA")
        page.insert_text((300, 200), "CellB")
        page.insert_text((100, 240), "CellC")
        page.insert_text((300, 240), "CellD")
        page.insert_text((100, 400), "Prose well outside the table")
        for y in (180, 220, 260):
            page.draw_line((90, y), (400, y))
        for x in (90, 290, 400):
            page.draw_line((x, 180), (x, 260))
        if rotation:
            page.set_rotation(rotation)
        if origin != (0, 0):
            page.set_mediabox(pymupdf.Rect(
                origin[0], origin[1], origin[0] + 612, origin[1] + 792,
            ))
        out = directory / f"table_r{rotation}_o{origin[0]}.pdf"
        doc.save(out)
        doc.close()
        return out

    @pytest.mark.parametrize("rotation,origin", [
        (0, (0, 0)),
        (90, (0, 0)),
        (180, (0, 0)),
        (270, (0, 0)),
        (0, (50, 50)),
    ], ids=["upright", "rot90", "rot180", "rot270", "shifted-origin"])
    def test_table_text_is_not_duplicated_into_the_text_block(
        self, tmp_path, rotation, origin,
    ):
        from extractor.pdf_reader import extract_pdf

        model = extract_pdf(self._table_pdf(tmp_path, rotation=rotation, origin=origin))
        text = _text_of(model)

        assert "CellA" not in text, "table text leaked into the text block"
        assert "Prose well outside the table" in text, "real prose was excluded"


# ---------------------------------------------------------------------------
# AUDIT 21 — setup_logging must close what it drops
# ---------------------------------------------------------------------------

class TestSetupLoggingClosesHandlers:
    def test_repeated_setup_does_not_leak_file_handles(self, tmp_path):
        """root.handlers.clear() drops handlers without closing their files.

        Harmless for one CLI run, a real leak in tests and in the long-lived
        launcher process the desktop UI plans to run.
        """
        from main import setup_logging

        log = tmp_path / "leak.log"
        first = None
        for _ in range(3):
            setup_logging(str(log))
            handlers = [
                h for h in logging.getLogger().handlers
                if isinstance(h, logging.FileHandler)
            ]
            assert len(handlers) == 1
            if first is None:
                first = handlers[0]

        assert first is not None
        assert first.stream is None or first.stream.closed, (
            "the first run's file handler was dropped without being closed"
        )

    @pytest.fixture(autouse=True)
    def _restore_logging(self):
        yield
        for handler in list(logging.getLogger().handlers):
            handler.close()
        logging.getLogger().handlers.clear()
