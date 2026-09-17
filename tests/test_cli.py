"""Tests for the CLI (argparse) in main.py — argument parsing and integration."""
from pathlib import Path

import pytest

from main import build_parser, main, unique_stems


class TestBuildParser:
    def test_default_values(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.input_dir == "input"
        assert args.output_dir == "output"
        assert args.log_file == "logs/extraction.log"
        assert args.max_file_mb == 100
        assert args.verbose is False
        # Off by default: Apple-Silicon only, and ~14 s/page. A Windows run
        # must not warn about a model it was never going to have.
        assert args.vlm is False
        assert args.vlm_pages == "auto"

    def test_vlm_pages_accepts_all(self):
        assert build_parser().parse_args(["--vlm", "--vlm-pages", "all"]).vlm_pages == "all"

    def test_custom_input_output(self):
        parser = build_parser()
        args = parser.parse_args(["--input", "docs", "--output", "out"])
        assert args.input_dir == "docs"
        assert args.output_dir == "out"

    def test_max_file_mb_override(self):
        parser = build_parser()
        args = parser.parse_args(["--max-file-mb", "10"])
        assert args.max_file_mb == 10

    def test_max_pages_flag_is_gone(self):
        """AUDIT 10: --max-pages was parsed, documented, and never enforced.

        A flag that silently does nothing is worse than no flag — it promises a
        safety limit callers do not get. Rejecting it says so out loud.
        """
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--max-pages", "100"])

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True
        args2 = parser.parse_args(["--verbose"])
        assert args2.verbose is True

    def test_log_file_override(self):
        parser = build_parser()
        args = parser.parse_args(["--log-file", "custom.log"])
        assert args.log_file == "custom.log"


class TestUniqueStems:
    def test_no_collision_keeps_bare_stem(self):
        paths = [Path("a.pdf"), Path("b.docx")]
        stems = unique_stems(paths)
        assert stems[paths[0]] == "a"
        assert stems[paths[1]] == "b"

    def test_collision_appends_extension(self):
        # AUDIT 1: report.pdf and report.docx used to both write report.json,
        # silently destroying one of the two documents.
        paths = [Path("report.docx"), Path("report.pdf")]
        stems = unique_stems(paths)
        assert stems[paths[0]] == "report-docx"
        assert stems[paths[1]] == "report-pdf"
        assert len(set(stems.values())) == 2


class TestMainIntegration:
    def test_colliding_stems_both_produce_output(self, tmp_path):
        # AUDIT 1: two inputs sharing a stem must not overwrite each other.
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "report.txt").write_text("text version", encoding="utf-8")
        (input_dir / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        output_dir = tmp_path / "out"
        main([
            "--input", str(input_dir),
            "--output", str(output_dir),
            "--log-file", str(tmp_path / "t.log"),
        ])
        json_dir = output_dir / "json"
        assert (json_dir / "report-txt.json").exists()
        assert (json_dir / "report-csv.json").exists()


    def test_nonexistent_input_dir_does_not_crash(self, tmp_path):
        """Running against a non-existent directory logs an error but doesn't raise."""
        main(["--input", str(tmp_path / "nonexistent"), "--log-file", str(tmp_path / "t.log")])

    def test_empty_input_dir_does_not_crash(self, tmp_path):
        """An empty directory logs info and exits cleanly."""
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        main(["--input", str(empty), "--log-file", str(tmp_path / "t.log")])


class TestExitCode:
    """AUDIT 9: main() returned None on every path, so sys.exit saw 0 always.

    A scheduler, CI step or the desktop launcher could not tell a run where
    every file failed from a clean one.
    """

    def test_clean_run_returns_zero(self, tmp_path):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "sample.txt").write_text("Hello", encoding="utf-8")

        code = main([
            "--input", str(input_dir),
            "--output", str(tmp_path / "out"),
            "--log-file", str(tmp_path / "t.log"),
        ])
        assert code == 0

    def test_missing_input_dir_returns_two(self, tmp_path):
        """The run could not start at all — distinct from a run that failed files."""
        code = main([
            "--input", str(tmp_path / "nonexistent"),
            "--log-file", str(tmp_path / "t.log"),
        ])
        assert code == 2

    def test_empty_input_dir_returns_zero(self, tmp_path):
        """Nothing to do is not a failure."""
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        code = main(["--input", str(empty), "--log-file", str(tmp_path / "t.log")])
        assert code == 0

    def test_failed_file_returns_one(self, tmp_path):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "big.csv").write_text("a,b\n" * 1000, encoding="utf-8")

        code = main([
            "--input", str(input_dir),
            "--output", str(tmp_path / "out"),
            "--max-file-mb", "0",
            "--log-file", str(tmp_path / "t.log"),
        ])
        assert code == 1

    def test_one_failure_among_successes_still_returns_one(self, tmp_path):
        """A partly-failed batch is not a clean run — the caller must see it."""
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "good.txt").write_text("Hello", encoding="utf-8")
        (input_dir / "broken.docx").write_bytes(b"not a real docx")

        code = main([
            "--input", str(input_dir),
            "--output", str(tmp_path / "out"),
            "--log-file", str(tmp_path / "t.log"),
        ])
        assert code == 1

    def test_oversized_file_reported_as_failed(self, tmp_path):
        """A file exceeding --max-file-mb is skipped, not crashed."""
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        big_file = input_dir / "big.csv"
        big_file.write_text("a,b\n" * 1000, encoding="utf-8")

        output_dir = tmp_path / "out"
        # Set max to 1 byte so the file is definitely oversized
        main([
            "--input", str(input_dir),
            "--output", str(output_dir),
            "--max-file-mb", "0",
            "--log-file", str(tmp_path / "t.log"),
        ])
        # No crash; JSON dir shouldn't have the file
        json_dir = output_dir / "json"
        if json_dir.exists():
            assert not (json_dir / "big.json").exists()

    def test_processes_supported_files_successfully(self, tmp_path):
        """A valid .txt file processes end-to-end via CLI."""
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "sample.txt").write_text("Hello from CLI test", encoding="utf-8")

        output_dir = tmp_path / "out"
        main([
            "--input", str(input_dir),
            "--output", str(output_dir),
            "--log-file", str(tmp_path / "t.log"),
        ])
        assert (output_dir / "json" / "sample.json").exists()
        assert (output_dir / "markdown" / "sample.md").exists()
