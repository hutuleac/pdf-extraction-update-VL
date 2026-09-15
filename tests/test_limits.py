"""Tests for extractor/limits.py — file size guards."""
import pytest

from extractor.limits import FileTooLargeError, check_file_size


class TestCheckFileSize:
    def test_small_file_passes(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_bytes(b"x" * 100)
        # Should not raise
        check_file_size(f, max_bytes=1024)

    def test_oversized_file_raises(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 2000)
        with pytest.raises(FileTooLargeError, match="exceeds limit"):
            check_file_size(f, max_bytes=1024)

    def test_exact_boundary_passes(self, tmp_path):
        f = tmp_path / "exact.txt"
        f.write_bytes(b"x" * 1024)
        # Exactly at the limit should pass (only > triggers)
        check_file_size(f, max_bytes=1024)

    def test_one_byte_over_raises(self, tmp_path):
        f = tmp_path / "over.txt"
        f.write_bytes(b"x" * 1025)
        with pytest.raises(FileTooLargeError):
            check_file_size(f, max_bytes=1024)

    def test_uses_default_when_max_bytes_none(self, tmp_path):
        f = tmp_path / "tiny.txt"
        f.write_bytes(b"x" * 10)
        # Default is 50 MB — a 10-byte file should always pass
        check_file_size(f, max_bytes=None)


class TestFileTooLargeError:
    def test_stores_context(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"x" * 100)
        exc = FileTooLargeError(f, size_bytes=2_000_000, max_bytes=1_000_000)
        assert exc.path == f
        assert exc.size_bytes == 2_000_000
        assert exc.max_bytes == 1_000_000

    def test_message_contains_filename_and_sizes(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"x")
        exc = FileTooLargeError(f, size_bytes=5_242_880, max_bytes=1_048_576)
        msg = str(exc)
        assert "report.pdf" in msg
        assert "5.0 MB" in msg
        assert "1 MB" in msg
