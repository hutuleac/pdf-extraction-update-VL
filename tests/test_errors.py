import pytest

from extractor.errors import ExtractionError, FatalErrorCategory, classify_exception
from extractor.limits import FileTooLargeError


def test_resource_limit_error_has_stable_category(tmp_path):
    exc = FileTooLargeError(tmp_path / "large.pdf", 20, 10)
    result = classify_exception(exc)
    assert isinstance(result, ExtractionError)
    assert result.category is FatalErrorCategory.RESOURCE_LIMIT
    assert str(result) == str(exc)


def test_unknown_exception_is_not_silently_reclassified():
    with pytest.raises(RuntimeError, match="bug"):
        classify_exception(RuntimeError("bug"))
